"""Group chat compliance: the audit trail, reports, legal hold, what patients agreed to, their data requests,
sharing earlier history in a care group, and the list of verified providers.

"Compliance" means the clinic's Enterprise Admin (for that clinic's groups) or the platform's Super Admin.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.chat_model import Conversation, ConversationParticipant
from app.models.group_chat_model import (
    GroupAuditLog, GroupConsent, GroupDataRequest, GroupReport, VerifiedProvider,
)
from app.services import group_chat_common as common
from app.services import group_chat_notify as notify


# ---------------------------------------------------------------- audit trail (read only)

def list_audit(
    db: Session, current_user: dict, conversation_id: UUID, *, action: str | None = None, target_user_id: UUID | None = None,
    page: int = 1, page_size: int = 50, access_token: str | None = None,
) -> dict:
    """Every admin action, role change, consent and message edit, newest first. Readable by the group's owner and by
    compliance. There is no way to change or delete an entry."""
    conversation = common.load_group(db, conversation_id)
    member = common.get_member(db, conversation.id, common.user_uuid(current_user))
    is_owner = member is not None and member.membership_status == "active" and member.group_role == "owner"
    if not is_owner and not common.is_compliance_admin(db, current_user, conversation, access_token):
        raise HTTPException(status_code=403, detail="Only the group owner and compliance can read the audit trail")
    query = db.query(GroupAuditLog).filter(GroupAuditLog.conversation_id == conversation.id)
    if action:
        query = query.filter(GroupAuditLog.action == action)
    if target_user_id:
        query = query.filter(GroupAuditLog.target_user_id == target_user_id)
    total = query.count()
    items = query.order_by(GroupAuditLog.created_at.desc(), GroupAuditLog.id).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total}


# ---------------------------------------------------------------- reports

def _compliance_recipients(db: Session, conversation: Conversation, access_token: str | None) -> list[UUID]:
    """The clinic's Enterprise Admins and the Platform Super Admins."""
    from app.services import event_notification_service as recipients

    ids: list[UUID] = []
    try:
        ids += recipients.resolve_platform_admin_user_ids(access_token)
    except Exception:
        pass
    try:
        class _Owner:  # resolve_enterprise_admin_user_ids reads the owning tenant from the item it is given
            enterprise = None
            tenant_id = conversation.tenant_id

        admins, _ = recipients.resolve_enterprise_admin_user_ids(db, _Owner(), access_token=access_token)
        ids += admins
    except Exception:
        pass
    return list(dict.fromkeys(ids))


def create_report(db: Session, current_user: dict, conversation_id: UUID, payload, access_token: str | None = None) -> GroupReport:
    """Anyone in the group can report a message or the group. It goes to the clinic's compliance team and the platform."""
    conversation = common.load_group(db, conversation_id)
    member = common.active_member(db, conversation, current_user)
    if payload.message_id is not None:
        from app.repository import chat_repo

        message = chat_repo.get_message_by_id(db, payload.message_id)
        if message is None or message.conversation_id != conversation.id or (
            member.history_from is not None and message.created_at <= member.history_from
        ):
            raise HTTPException(status_code=404, detail="Message not found")
    report = GroupReport(
        conversation_id=conversation.id, tenant_id=conversation.tenant_id, message_id=payload.message_id,
        reporter_id=member.user_id, reason=payload.reason, details=payload.details,
    )
    db.add(report)
    db.flush()
    common.audit(db, conversation, actor=current_user, action="report_filed", message_id=payload.message_id,
                 details={"report_id": str(report.id), "reason": payload.reason})
    db.commit()
    db.refresh(report)
    notify.notify_report(db, conversation, _compliance_recipients(db, conversation, access_token), report.id, payload.message_id is not None)
    return report


def _report_scope(db: Session, current_user: dict, access_token: str | None):
    """Super Admin: every report. Enterprise Admin: the clinic's. Anyone else: not allowed."""
    if common.is_super_admin(current_user):
        return None
    if common.platform_role(current_user) == "admin":
        tenant = common.caller_tenant(db, current_user, access_token)
        if tenant is not None:
            return tenant
    raise HTTPException(status_code=403, detail="Only compliance can read group reports")


def list_reports(
    db: Session, current_user: dict, *, status: str | None = None, conversation_id: UUID | None = None, page: int = 1,
    page_size: int = 50, access_token: str | None = None,
) -> dict:
    tenant = _report_scope(db, current_user, access_token)
    query = db.query(GroupReport)
    if tenant is not None:
        query = query.filter(GroupReport.tenant_id == tenant)
    if status:
        query = query.filter(GroupReport.status == status)
    if conversation_id:
        query = query.filter(GroupReport.conversation_id == conversation_id)
    total = query.count()
    items = query.order_by(GroupReport.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total}


def resolve_report(db: Session, current_user: dict, report_id: UUID, payload, access_token: str | None = None) -> GroupReport:
    tenant = _report_scope(db, current_user, access_token)
    report = db.query(GroupReport).filter(GroupReport.id == report_id).first()
    if report is None or (tenant is not None and report.tenant_id != tenant):
        raise HTTPException(status_code=404, detail="Report not found")
    conversation = db.query(Conversation).filter(Conversation.id == report.conversation_id).first()
    report.status, report.resolution_note = payload.status, payload.note
    if payload.status in ("resolved", "dismissed"):
        report.resolved_by, report.resolved_at = common.user_uuid(current_user), datetime.utcnow()
    if conversation is not None:
        common.audit(db, conversation, actor=current_user, action="report_" + payload.status, message_id=report.message_id,
                     reason=payload.note, details={"report_id": str(report.id)})
    db.commit()
    db.refresh(report)
    return report


# ---------------------------------------------------------------- legal hold

def set_legal_hold(db: Session, current_user: dict, conversation_id: UUID, payload, access_token: str | None = None) -> dict:
    conversation = common.load_group(db, conversation_id)
    if not common.is_compliance_admin(db, current_user, conversation, access_token):
        raise HTTPException(status_code=403, detail="Only compliance can put a group on legal hold")
    conversation.legal_hold, conversation.legal_hold_reason = True, payload.reason
    common.audit(db, conversation, actor=current_user, action="legal_hold_set", reason=payload.reason)
    db.commit()
    return {"conversation_id": conversation.id, "legal_hold": True, "reason": payload.reason}


def clear_legal_hold(db: Session, current_user: dict, conversation_id: UUID, access_token: str | None = None) -> dict:
    conversation = common.load_group(db, conversation_id)
    if not common.is_compliance_admin(db, current_user, conversation, access_token):
        raise HTTPException(status_code=403, detail="Only compliance can lift a legal hold")
    if conversation.legal_hold:
        common.audit(db, conversation, actor=current_user, action="legal_hold_cleared", reason=conversation.legal_hold_reason)
        conversation.legal_hold, conversation.legal_hold_reason = False, None
        db.commit()
    return {"conversation_id": conversation.id, "legal_hold": False, "reason": None}


# ---------------------------------------------------------------- compliance view of all groups

def list_groups_for_compliance(
    db: Session, current_user: dict, *, kind: str | None = None, status: str | None = None, legal_hold: bool | None = None,
    page: int = 1, page_size: int = 50, access_token: str | None = None,
) -> dict:
    tenant = _report_scope(db, current_user, access_token)
    query = db.query(Conversation).filter(Conversation.conversation_type == common.GROUP_TYPE, Conversation.is_deleted.is_(False))
    if tenant is not None:
        query = query.filter(Conversation.tenant_id == tenant)
    if kind:
        query = query.filter(Conversation.group_kind == kind)
    if status:
        query = query.filter(Conversation.status == status)
    if legal_hold is not None:
        query = query.filter(Conversation.legal_hold.is_(legal_hold))
    total = query.count()
    rows = query.order_by(Conversation.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    ids = [c.id for c in rows]
    counts = dict(
        db.query(ConversationParticipant.conversation_id, func.count(ConversationParticipant.id))
        .filter(ConversationParticipant.conversation_id.in_(ids), ConversationParticipant.membership_status == "active")
        .group_by(ConversationParticipant.conversation_id).all()
    ) if ids else {}
    from app.services.group_chat_service import group_dict
    from app.services.group_chat_training import context_names

    titles = context_names(db, rows)
    return {"items": [group_dict(db, c, None, active_count=counts.get(c.id, 0), context_name=titles.get(c.id)) for c in rows], "total": total}


# ---------------------------------------------------------------- what a patient agreed to, and their data

def list_my_consents(db: Session, current_user: dict) -> dict:
    user_id = common.user_uuid(current_user)
    rows = (
        db.query(GroupConsent, Conversation.subject)
        .outerjoin(Conversation, Conversation.id == GroupConsent.conversation_id)
        .filter(GroupConsent.user_id == user_id).order_by(GroupConsent.granted_at.desc()).all()
    )
    return {"items": [
        {"conversation_id": c.conversation_id, "group_name": name, "granted_at": c.granted_at, "withdrawn_at": c.withdrawn_at,
         "is_minor": c.is_minor, "guardian_name": c.guardian_name, "guardian_relationship": c.guardian_relationship, "notice": c.notice}
        for c, name in rows
    ]}


def create_data_request(db: Session, current_user: dict, payload) -> GroupDataRequest:
    user_id = common.user_uuid(current_user)
    if payload.conversation_id is not None:
        member = common.get_member(db, payload.conversation_id, user_id)
        if member is None:
            raise HTTPException(status_code=404, detail="Group not found")
    request = GroupDataRequest(user_id=user_id, conversation_id=payload.conversation_id, note=payload.note)
    db.add(request)
    db.commit()
    db.refresh(request)
    return request


def list_my_data_requests(db: Session, current_user: dict) -> list[GroupDataRequest]:
    return (
        db.query(GroupDataRequest).filter(GroupDataRequest.user_id == common.user_uuid(current_user))
        .order_by(GroupDataRequest.created_at.desc()).all()
    )


def grievance_officer() -> dict:
    name, email, phone = settings.GROUP_GRIEVANCE_OFFICER_NAME, settings.GROUP_GRIEVANCE_OFFICER_EMAIL, settings.GROUP_GRIEVANCE_OFFICER_PHONE
    return {"name": name or None, "email": email or None, "phone": phone or None, "configured": bool(name or email or phone)}


# ---------------------------------------------------------------- sharing earlier history in a care group

def set_history_sharing_consent(db: Session, current_user: dict, conversation_id: UUID, agree: bool) -> dict:
    """A patient in a care group agrees (or stops agreeing) that the owner may share earlier history with a new member of
    the care team. Never available in a program group."""
    conversation = common.load_group(db, conversation_id)
    if conversation.group_kind != "care":
        raise HTTPException(status_code=409, detail="Earlier history is never shared in a program group")
    member = common.active_member(db, conversation, current_user)
    if member.member_kind != "patient":
        raise HTTPException(status_code=403, detail="Only the patient can agree to share earlier history")
    member.history_sharing_consent_at = datetime.utcnow() if agree else None
    common.audit(db, conversation, actor=current_user, action="history_sharing_agreed" if agree else "history_sharing_withdrawn",
                 target_user_id=member.user_id)
    db.commit()
    return {"conversation_id": conversation.id, "agreed": agree}


def share_history(db: Session, current_user: dict, conversation_id: UUID, target_id: UUID, payload) -> dict:
    """The owner lets a member of the care team see the messages from before they joined, for a clinical reason. Only
    where the patient has agreed; the share is written to the audit trail."""
    conversation = common.load_group(db, conversation_id)
    if conversation.group_kind != "care":
        raise HTTPException(status_code=409, detail="Earlier history is never shared in a program group")
    owner = common.active_member(db, conversation, current_user)
    if owner.group_role != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can share earlier history")
    target = common.get_member(db, conversation.id, target_id)
    if target is None or target.membership_status != "active":
        raise HTTPException(status_code=404, detail="Member not found")
    if target.member_kind != "provider":
        raise HTTPException(status_code=422, detail="Earlier history can only be shared with a provider on the care team")
    patients = [m for m in common.active_members(db, conversation.id) if m.member_kind == "patient"]
    if not patients or any(p.history_sharing_consent_at is None for p in patients):
        raise HTTPException(status_code=403, detail="The patient has not agreed to share earlier history")
    previous = target.history_from
    target.history_from = conversation.created_at
    target.history_shared_by = owner.user_id
    common.audit(db, conversation, actor=current_user, action="history_shared", target_user_id=target_id, reason=payload.reason,
                 details={"visible_from": conversation.created_at.isoformat(), "previously_from": previous.isoformat() if previous else None})
    db.commit()
    return {"conversation_id": conversation.id, "user_id": target_id, "history_from": target.history_from}


# ---------------------------------------------------------------- verified providers

def _verification_scope(db: Session, current_user: dict, access_token: str | None, requested_tenant: UUID | None) -> UUID:
    """The clinic whose providers the caller is allowed to manage."""
    if common.is_super_admin(current_user):
        if requested_tenant is None:
            raise HTTPException(status_code=422, detail="tenant_id is required when the platform verifies a provider")
        return requested_tenant
    if common.platform_role(current_user) == "admin":
        tenant = common.caller_tenant(db, current_user, access_token)
        if tenant is not None:
            if requested_tenant not in (None, tenant):
                raise HTTPException(status_code=403, detail="You can only verify providers of your own clinic")
            return tenant
    raise HTTPException(status_code=403, detail="Only a clinic admin or the platform can verify providers")


def _assert_active_provider_in_tenant(user_id: UUID, tenant_id: UUID, access_token: str | None) -> None:
    """Confirm the target is an active Provider in the clinic using Identity's member source of truth."""
    from app.services import event_notification_service as members

    provider_roles = {"provider", "tenant_admin", "internal_user", "contributor", "reviewer", "publisher", "staff"}
    for member in members._lookup(members.list_tenant_users, tenant_id, access_token=access_token) or []:
        if members._user_id(member) != user_id:
            continue
        if members._active(member) and members._roles(member) & provider_roles:
            return
        raise HTTPException(status_code=422, detail="The target user is not an active Provider in this clinic")
    raise HTTPException(status_code=422, detail="The target user is not a member of this clinic")


def verify_provider(db: Session, current_user: dict, payload, access_token: str | None = None) -> VerifiedProvider:
    """Record that a provider's registration has been checked. Needed before they can create or own a group."""
    tenant = _verification_scope(db, current_user, access_token, payload.tenant_id)
    _assert_active_provider_in_tenant(payload.user_id, tenant, access_token)
    row = db.query(VerifiedProvider).filter(VerifiedProvider.user_id == payload.user_id).first()
    if row is not None and row.tenant_id not in (None, tenant):
        raise HTTPException(status_code=409, detail="This provider is already registered with another clinic")
    if row is None:
        row = VerifiedProvider(user_id=payload.user_id)
        db.add(row)
    row.tenant_id = tenant
    row.registration_number, row.registration_council, row.notes = payload.registration_number, payload.registration_council, payload.notes
    row.status, row.verified_by, row.verified_at, row.revoked_at = "verified", common.user_uuid(current_user), datetime.utcnow(), None
    db.commit()
    db.refresh(row)
    return row


def list_verified_providers(db: Session, current_user: dict, access_token: str | None = None) -> list[VerifiedProvider]:
    query = db.query(VerifiedProvider).filter(VerifiedProvider.status == "verified")
    if not common.is_super_admin(current_user):
        tenant = _verification_scope(db, current_user, access_token, None)
        query = query.filter(VerifiedProvider.tenant_id == tenant)
    return query.order_by(VerifiedProvider.verified_at.desc()).all()


def revoke_provider(db: Session, current_user: dict, user_id: UUID, access_token: str | None = None) -> VerifiedProvider:
    # The tenant is derived from the stored verification for a Super Admin;
    # clinic admins remain limited to their own tenant.
    if common.is_super_admin(current_user):
        tenant = None
    else:
        tenant = _verification_scope(db, current_user, access_token, None)
    row = db.query(VerifiedProvider).filter(VerifiedProvider.user_id == user_id).first()
    if row is None or (tenant is not None and row.tenant_id != tenant):
        raise HTTPException(status_code=404, detail="Provider not found")
    row.status, row.revoked_at = "revoked", datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row
