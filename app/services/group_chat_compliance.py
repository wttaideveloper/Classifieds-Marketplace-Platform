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
    return {"items": [group_dict(db, c, None, active_count=counts.get(c.id, 0), context_name=titles.get(c.id), compliance=True) for c in rows], "total": total}


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

def _verification_scope(db: Session, current_user: dict, access_token: str | None) -> UUID | None:
    """Super Admin: any clinic. Enterprise Admin: their own clinic's providers."""
    if common.is_super_admin(current_user):
        return None
    if common.platform_role(current_user) == "admin":
        tenant = common.caller_tenant(db, current_user, access_token)
        if tenant is not None:
            return tenant
    raise HTTPException(status_code=403, detail="Only a clinic admin or the platform can verify providers")


_PROVIDER_ROLES = {"provider", "tenant_admin", "internal_user", "staff"}    # the picker's default list

# Roles of a clinic member that may be verified for group chat: clinicians and the clinic's own administrators (an owner or
# admin who also teaches, or who is to be made an admin of a Provider's group). A learner (external_user) is not.
_VERIFIABLE_ROLES = _PROVIDER_ROLES | {"tenant_owner", "enterprise_admin"}


def _assert_clinic_member(user_id: UUID, tenant: UUID, access_token: str | None) -> None:
    """The person must be an active member of the clinic, with a role that can be verified. The clinic's member list
    comes from the identity service (the same source as the picker)."""
    from app.services import event_notification_service as members

    records = members.owning_tenant_users(tenant, access_token)[0] or []
    if not records:    # the lookup returns nothing when it fails; a clinic whose admin is calling is never empty
        raise HTTPException(status_code=503, detail="The clinic's members could not be checked right now. Try again shortly")
    mine = [r for r in records if members._user_id(r) == user_id]
    if not mine:
        raise HTTPException(status_code=422, detail="The user is not a member of this clinic")
    active = [r for r in mine if members._active(r)]
    if not active:
        raise HTTPException(status_code=422, detail="The user's membership in this clinic is not active")
    if not any(members._roles(r) & _VERIFIABLE_ROLES for r in active):
        raise HTTPException(status_code=422, detail="Only a clinic provider, admin or owner can be verified, not this user's role")


def verify_provider(db: Session, current_user: dict, payload, access_token: str | None = None) -> VerifiedProvider:
    """Record that a provider's registration has been checked. Needed before they can create or own a group.

    The person must be an active member of the clinic they are verified for (provider, admin or owner roles)."""
    tenant = _verification_scope(db, current_user, access_token)
    if tenant is not None and payload.tenant_id not in (None, tenant):
        raise HTTPException(status_code=403, detail="You can only verify providers of your own clinic")
    row = db.query(VerifiedProvider).filter(VerifiedProvider.user_id == payload.user_id).first()
    clinic = payload.tenant_id or tenant or (row.tenant_id if row is not None else None)
    if clinic is None:
        raise HTTPException(status_code=422, detail="tenant_id is required")
    _assert_clinic_member(payload.user_id, clinic, access_token)
    if row is None:
        row = VerifiedProvider(user_id=payload.user_id)
        db.add(row)
    row.tenant_id = clinic
    row.registration_number, row.registration_council, row.notes = payload.registration_number, payload.registration_council, payload.notes
    row.status, row.verified_by, row.verified_at, row.revoked_at = "verified", common.user_uuid(current_user), datetime.utcnow(), None
    db.commit()
    db.refresh(row)
    return with_names(db, [row], current_user)[0]


def _provider_dict(row: VerifiedProvider, names: dict) -> dict:
    return {
        "user_id": row.user_id, "name": names.get(str(row.user_id)), "tenant_id": row.tenant_id,
        "registration_number": row.registration_number, "registration_council": row.registration_council,
        "status": row.status, "verified_by": row.verified_by, "verified_at": row.verified_at, "revoked_at": row.revoked_at,
        "notes": row.notes,
    }


def with_names(db: Session, rows: list[VerifiedProvider], current_user: dict) -> list[dict]:
    """The rows as plain dicts with the provider's display name (one cached lookup for the lot)."""
    from types import SimpleNamespace

    tenants = {r.tenant_id for r in rows if r.tenant_id}
    names = common.resolve_names(
        db, SimpleNamespace(tenant_id=next(iter(tenants)) if len(tenants) == 1 else None), [r.user_id for r in rows], current_user,
    ) if rows else {}
    return [_provider_dict(r, names) for r in rows]


def list_verified_providers(db: Session, current_user: dict, access_token: str | None = None) -> list[dict]:
    tenant = _verification_scope(db, current_user, access_token)
    query = db.query(VerifiedProvider)
    if tenant is not None:
        query = query.filter(VerifiedProvider.tenant_id == tenant)
    return with_names(db, query.order_by(VerifiedProvider.verified_at.desc()).all(), current_user)


def list_provider_candidates(
    db: Session, current_user: dict, access_token: str | None = None, *, tenant_id: UUID | None = None, q: str | None = None,
    include_verified: bool = False, all_roles: bool = False, page: int = 1, page_size: int = 50,
) -> dict:
    """The clinic's members that can be verified, for the "Add provider" picker. Source: the identity service's member
    list of the clinic. By default only active members with a provider-type role who are not verified yet; `all_roles`
    lists every active member (to verify an Enterprise Admin who is also a clinician) and `include_verified` also lists
    the people who are verified now. Someone whose verification was revoked is always listed, marked `revoked`, so they can be
    verified again."""
    from app.services import chat_user_names
    from app.services import event_notification_service as members

    scope = _verification_scope(db, current_user, access_token)
    tenant = scope or tenant_id
    if tenant is None:
        raise HTTPException(status_code=422, detail="tenant_id is required")
    if scope is not None and tenant_id not in (None, scope):
        raise HTTPException(status_code=403, detail="You can only list the members of your own clinic")

    records = members.owning_tenant_users(tenant, access_token)[0] or []
    found: dict = {}
    for record in records:
        user_id = members._user_id(record)
        if user_id is None or user_id in found or not members._active(record):
            continue
        roles = members._roles(record)
        if not all_roles and not (roles & _PROVIDER_ROLES):
            continue
        found[user_id] = {"name": chat_user_names.extract_display_name(record), "roles": sorted(roles)}

    existing = {
        r.user_id: r for r in db.query(VerifiedProvider).filter(VerifiedProvider.user_id.in_(list(found))).all()
    } if found else {}
    unnamed = [str(u) for u, v in found.items() if not v["name"]]
    if unnamed:
        from types import SimpleNamespace

        looked_up = common.resolve_names(db, SimpleNamespace(tenant_id=tenant), unnamed, current_user)
        for u, v in found.items():
            v["name"] = v["name"] or looked_up.get(str(u))

    needle = (q or "").strip().lower()
    items = []
    for user_id, info in found.items():
        row = existing.get(user_id)
        state = "not_verified" if row is None else row.status
        if state == "verified" and not include_verified:
            continue      # a revoked provider stays listed (marked revoked) so they can be verified again
        if needle and needle not in (info["name"] or "").lower() and not any(needle in r for r in info["roles"]):
            continue
        items.append({
            "user_id": user_id, "name": info["name"], "roles": info["roles"], "verification": state,
            "registration_number": row.registration_number if row is not None and row.status == "verified" else None,
        })
    items.sort(key=lambda i: ((i["name"] or "~").lower(), str(i["user_id"])))
    start = (page - 1) * page_size
    return {"tenant_id": tenant, "items": items[start:start + page_size], "total": len(items), "page": page, "page_size": page_size}


def revoke_provider(db: Session, current_user: dict, user_id: UUID, access_token: str | None = None) -> VerifiedProvider:
    tenant = _verification_scope(db, current_user, access_token)
    row = db.query(VerifiedProvider).filter(VerifiedProvider.user_id == user_id).first()
    if row is None or (tenant is not None and row.tenant_id != tenant):
        raise HTTPException(status_code=404, detail="Provider not found")
    row.status, row.revoked_at = "revoked", datetime.utcnow()
    db.commit()
    db.refresh(row)
    return with_names(db, [row], current_user)[0]
