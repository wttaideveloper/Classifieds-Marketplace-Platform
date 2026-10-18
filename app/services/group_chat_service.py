"""Group chat: creating a group, inviting people, consent, members, roles, ownership, closing.

Rules this file enforces (docs/group-chat-api.md has the full list and the endpoint contract):

* Only a verified provider or a clinic admin creates a group, always linked to a program, appointment series or care
  episode. The creator is the owner and cannot be removed by other admins.
* Nobody is added without consent. An invited person has status `invited`, sees nothing in the group, and becomes a
  member only by accepting (for someone under 18, with a parent's or guardian's consent). Consent is recorded and can
  be withdrawn.
* Only a verified provider can be an admin or the owner. At most GROUP_MAX_ADMINS admins besides the owner.
* Nothing is erased: removing or leaving only changes the member's status.
* Every change is written to the audit trail in the same transaction.
"""
from __future__ import annotations

import logging
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.chat_model import Conversation, ConversationParticipant, Message
from app.models.group_chat_model import GroupConsent
from app.repository import chat_repo
from app.services import group_chat_common as common
from app.services import group_chat_notify as notify
from app.services import group_chat_training as training_groups

_UNSET = object()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- helpers

def _link_exists(db: Session, link_type: str, link_id: UUID, tenant_id: UUID | None) -> bool:
    """A program must exist (and belong to the clinic). Appointment series and care episodes have no table of their
    own in this service yet, so only their type is checked."""
    if link_type != "program":
        return True
    from app.models.program_model import Program

    program = db.query(Program).filter(Program.id == link_id).first()
    if program is None:
        return False
    return program.tenant_id is None or tenant_id is None or program.tenant_id == tenant_id


def system_message(db: Session, conversation: Conversation, actor_id: UUID | None, text: str) -> Message:
    """A line in the chat such as "Dr. Rao added Priya". The text never contains health information: it names people
    (patients by their display name) and says what happened."""
    message = Message(
        conversation_id=conversation.id, sender_id=actor_id or conversation.created_by, content=text, message_type="system",
    )
    db.add(message)
    db.flush()
    conversation.last_message_at = message.created_at or datetime.utcnow()
    conversation.last_message_preview = text[:500]
    return message


def short_name(member: ConversationParticipant, full_names: dict[str, str | None]) -> str:
    """The name used in system messages: a patient is shown by display name, a provider by full name."""
    full = full_names.get(str(member.user_id))
    if member.member_kind == "patient":
        return member.display_name or common.default_display_name(full) or "A member"
    return full or member.display_name or "A provider"


def _unread_count(db: Session, conversation: Conversation, member: ConversationParticipant) -> int:
    since = member.last_read_at or member.history_from
    query = db.query(func.count(Message.id)).filter(
        Message.conversation_id == conversation.id,
        Message.sender_id != member.user_id,
        Message.is_deleted.is_(False),
        Message.hidden_kind.is_(None),
        Message.message_type != "system",
    )
    if since is not None:
        query = query.filter(Message.created_at > since)
    if member.history_from is not None:
        query = query.filter(Message.created_at > member.history_from)
    return int(query.scalar() or 0)


def _last_visible_message(db: Session, conversation: Conversation, member: ConversationParticipant) -> Message | None:
    query = db.query(Message).filter(
        Message.conversation_id == conversation.id, Message.is_deleted.is_(False), Message.hidden_kind.is_(None),
    )
    if member.history_from is not None:
        query = query.filter(Message.created_at > member.history_from)
    return query.order_by(Message.created_at.desc(), Message.id.desc()).first()


def permissions_for(conversation: Conversation, member: ConversationParticipant | None, *, compliance: bool = False) -> dict:
    """What the caller can do with the group now, from the same rules the endpoints enforce. `compliance` is true for the
    clinic's Enterprise Admin of this group, or the Super Admin (they may act on a group without being in it)."""
    from fastapi import HTTPException as _Http

    active = member is not None and member.membership_status == "active"
    is_admin = active and common.is_admin(member)
    is_owner = active and member.group_role == "owner"
    is_open = conversation.status == "open" and not conversation.is_read_only
    can_post = False
    if active:
        from app.services.group_chat_messages import assert_can_post

        try:
            assert_can_post(conversation, member)
            can_post = True
        except _Http:
            can_post = False
    return {
        "can_post": can_post,
        "can_edit_group": is_admin and is_open,
        "can_change_settings": is_admin and is_open,
        "can_invite": is_admin and is_open,
        "can_view_members": active,
        "can_view_pending_invitations": is_admin,
        "can_remove_members": is_admin or compliance,
        "can_make_admin": is_admin or compliance,
        "can_remove_admin": is_owner or compliance,
        "can_mute_members": is_admin,
        "can_hide_messages": is_admin,
        "can_pin_messages": active and member.member_kind == "provider",
        "can_close": is_admin and is_open,
        "can_reopen": (is_owner or compliance) and conversation.status == "closed",
        "can_transfer_ownership": is_owner,
        "can_reassign_owner": compliance,
        "can_leave": active and not is_owner,
        "can_view_audit": is_owner or compliance,
        "can_set_legal_hold": compliance,
        "can_report": active,
        "can_mute_group": active,
    }


def group_dict(
    db: Session, conversation: Conversation, member: ConversationParticipant | None, *, active_count: int | None = None,
    context_name=_UNSET, compliance: bool = False,
) -> dict:
    """GroupResponse as a dict, for the person `member` (None for a compliance admin who is not in the group)."""
    count = active_count if active_count is not None else common.count_active(db, conversation.id)
    if context_name is _UNSET:
        context_name = training_groups.context_names(db, [conversation]).get(conversation.id)
    last = _last_visible_message(db, conversation, member) if member is not None else None
    return {
        "id": conversation.id, "name": conversation.subject or "", "kind": conversation.group_kind or "program",
        "description": conversation.description, "avatar_url": conversation.avatar_url,
        "context_type": conversation.context_type, "context_id": conversation.context_id, "context_name": context_name,
        "tenant_id": conversation.tenant_id, "status": "closed" if conversation.status != "open" else "open",
        "admins_only_posting": bool(conversation.admins_only_posting), "created_by": conversation.created_by,
        "created_at": conversation.created_at, "member_count": count, "visibility_notice": common.visibility_notice(count),
        "emergency_notice": common.emergency_notice(), "forwarding_blocked": True, "screenshots_blocked": True,
        "limits": {
            "max_members": common.max_members(conversation.group_kind), "max_admins": settings.GROUP_MAX_ADMINS,
            "edit_window_minutes": settings.GROUP_EDIT_WINDOW_MINUTES,
        },
        "my_role": member.group_role if member else None, "my_kind": member.member_kind if member else None,
        "permissions": permissions_for(conversation, member, compliance=compliance),
        "muted": common.member_is_muted_now(member) if member else False, "pinned": bool(member.pinned) if member else False,
        "is_archived": bool(member.is_archived) if member else False,
        "unread_count": _unread_count(db, conversation, member) if member else 0,
        "last_message_at": last.created_at if last else None,
        "last_message_preview": chat_repo.message_list_preview(last) if last else None,
        "closed_at": conversation.closed_at, "closed_reason": conversation.closed_reason,
        "retention_started_at": conversation.retention_started_at, "retention_until": common.retention_until(conversation),
        "legal_hold": bool(conversation.legal_hold),
    }


def _invitee_rows(db: Session, conversation: Conversation, invitees, inviter_id: UUID) -> tuple[list[ConversationParticipant], list[UUID]]:
    """Create or revive the `invited` rows for these people. Returns (rows, skipped ids). Someone already active or
    already invited is skipped; someone who declined, left or was removed can be invited again."""
    rows, skipped, seen = [], [], set()
    now = datetime.utcnow()
    for invitee in invitees:
        uid = invitee.user_id
        if uid == inviter_id or uid in seen:
            skipped.append(uid)
            continue
        seen.add(uid)
        existing = common.get_member(db, conversation.id, uid)
        if existing is not None and existing.membership_status in ("active", "invited"):
            skipped.append(uid)
            continue
        kind = "provider" if common.is_verified_provider(db, uid, conversation.tenant_id) else "patient"
        row = existing or ConversationParticipant(conversation_id=conversation.id, user_id=uid)
        row.role = "provider" if kind == "provider" else "customer"
        row.membership_status = "invited"
        row.group_role = "member"
        row.member_kind = kind
        row.display_name = common.clean_display_name(invitee.display_name)
        row.is_minor = bool(invitee.is_minor)
        row.invited_by, row.invited_at, row.responded_at = inviter_id, now, None
        row.left_at = row.removed_by = row.removed_reason = None
        row.history_from = None
        row.posting_muted, row.posting_muted_until = False, None
        if existing is None:
            db.add(row)
        rows.append(row)
    return rows, skipped


def _capacity_check(db: Session, conversation: Conversation, extra: int) -> None:
    held = common.count_active(db, conversation.id) + common.count_pending(db, conversation.id)
    limit = common.max_members(conversation.group_kind)
    if held + extra > limit:
        raise HTTPException(
            status_code=409,
            detail=f"A {conversation.group_kind} group can have at most {limit} members (including pending invitations)",
        )


# ---------------------------------------------------------------- create / read / update

def create_group(db: Session, current_user: dict, payload, access_token: str | None = None) -> dict:
    user_id = common.user_uuid(current_user)
    tenant_id = common.caller_tenant(db, current_user, access_token)
    if tenant_id is None:
        raise HTTPException(status_code=403, detail="Authenticated tenant identity required to create a group")
    common.assert_can_create_groups(db, current_user, tenant_id)
    training = None
    if payload.context_type == training_groups.LINK_TYPE:
        training = training_groups.assert_can_link(db, current_user, payload.context_id, access_token)
    elif not _link_exists(db, payload.context_type, payload.context_id, tenant_id):
        raise HTTPException(status_code=422, detail=f"The linked {payload.context_type.replace('_', ' ')} was not found")

    invitees = [i for i in payload.invitees if i.user_id != user_id]
    unique = {i.user_id: i for i in invitees}
    invitees = list(unique.values())
    if training is not None:
        training_groups.check_invitees(db, training, tenant_id, [i.user_id for i in invitees], access_token)
    if 1 + len(invitees) > common.max_members(payload.kind):
        raise HTTPException(status_code=409, detail=f"A {payload.kind} group can have at most {common.max_members(payload.kind)} members")
    if training is not None and payload.invite_all_enrolled:
        room = common.max_members(payload.kind) - 1 - len(invitees)
        picked, _stats = training_groups.pick_eligible(
            db, None, training, user_id, access_token, limit=room,
            exclude=set(payload.exclude_user_ids), already_taken={i.user_id for i in invitees},
        )
        invitees = invitees + picked

    full = common.resolve_names(db, Conversation(tenant_id=tenant_id), [i.user_id for i in invitees], current_user)
    common.assert_name_allowed(db, None, payload.name, [n for n in full.values() if n])

    now = datetime.utcnow()
    admins_only = payload.admins_only_posting if payload.admins_only_posting is not None else payload.kind == "program"
    conversation = Conversation(
        tenant_id=tenant_id, status="open", conversation_type=common.GROUP_TYPE, subject=payload.name,
        group_kind=payload.kind, description=payload.description, avatar_url=payload.avatar_url,
        context_type=payload.context_type, context_id=payload.context_id, created_by=user_id,
        admins_only_posting=bool(admins_only), last_message_at=now,
    )
    db.add(conversation)
    db.flush()
    owner = ConversationParticipant(
        conversation_id=conversation.id, user_id=user_id, role="provider", membership_status="active", group_role="owner",
        member_kind="provider", joined_at=now, history_from=now, responded_at=now,
    )
    db.add(owner)
    rows, _skipped = _invitee_rows(db, conversation, invitees, user_id)
    common.audit(
        db, conversation, actor=current_user, action="group_created",
        details={"name": payload.name, "kind": payload.kind, "context_type": payload.context_type,
                 "context_id": str(payload.context_id), "invited": len(rows), "admins_only_posting": bool(admins_only)},
    )
    for row in rows:
        common.audit(db, conversation, actor=current_user, action="member_invited", target_user_id=row.user_id,
                     details={"member_kind": row.member_kind, "is_minor": row.is_minor})
    owner_names = common.resolve_names(db, conversation, [user_id], current_user)
    system_message(db, conversation, user_id, f"{short_name(owner, owner_names)} created the group")
    db.commit()
    db.refresh(conversation)

    notify.notify_invitation(db, conversation, [r.user_id for r in rows], owner_names.get(str(user_id)))
    for row in rows:
        notify.emit_user(row.user_id, "group_invitation", {"conversation_id": str(conversation.id)})
    notify.sync_rooms(user_id, conversation.id, join=True)
    return group_dict(db, conversation, owner)


def get_group(db: Session, current_user: dict, conversation_id: UUID, access_token: str | None = None) -> dict:
    conversation = common.load_group(db, conversation_id)
    member = common.get_member(db, conversation.id, common.user_uuid(current_user))
    compliance = common.is_compliance_admin(db, current_user, conversation, access_token)
    if member is not None and member.membership_status == "active":
        return group_dict(db, conversation, member, compliance=compliance)
    if compliance:
        return group_dict(db, conversation, None, compliance=True)  # read the group's details, not its messages
    raise HTTPException(status_code=403, detail="You are not a member of this group")


def list_groups(
    db: Session, current_user: dict, *, kind: str | None = None, archived: bool = False, search: str | None = None,
    page: int = 1, page_size: int = 50,
) -> dict:
    user_id = common.user_uuid(current_user)
    query = (
        db.query(Conversation, ConversationParticipant)
        .join(ConversationParticipant, ConversationParticipant.conversation_id == Conversation.id)
        .filter(
            Conversation.conversation_type == common.GROUP_TYPE, Conversation.is_deleted.is_(False),
            ConversationParticipant.user_id == user_id, ConversationParticipant.membership_status == "active",
            ConversationParticipant.is_archived.is_(archived),
        )
    )
    if kind:
        query = query.filter(Conversation.group_kind == kind)
    if search:
        query = query.filter(Conversation.subject.ilike(f"%{search.strip()}%"))
    total = query.count()
    rows = (
        query.order_by(ConversationParticipant.pinned.desc(), Conversation.last_message_at.desc().nullslast(), Conversation.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size).all()
    )
    ids = [c.id for c, _ in rows]
    counts = dict(
        db.query(ConversationParticipant.conversation_id, func.count(ConversationParticipant.id))
        .filter(ConversationParticipant.conversation_id.in_(ids), ConversationParticipant.membership_status == "active")
        .group_by(ConversationParticipant.conversation_id).all()
    ) if ids else {}
    titles = training_groups.context_names(db, [c for c, _ in rows])
    return {"items": [group_dict(db, c, m, active_count=counts.get(c.id, 0), context_name=titles.get(c.id)) for c, m in rows], "total": total}


def _invitation_dict(db: Session, conversation: Conversation, member: ConversationParticipant, current_user: dict) -> dict:
    active = common.active_members(db, conversation.id)
    inviter_name = None
    provider_ids = [m.user_id for m in active if m.member_kind == "provider"]
    names = common.resolve_names(db, conversation, provider_ids + ([member.invited_by] if member.invited_by else []), current_user)
    provider_names = sorted({names.get(str(uid)) for uid in provider_ids if names.get(str(uid))})
    if member.invited_by:
        inviter_name = names.get(str(member.invited_by))
    kind_text = "one patient's care team" if conversation.group_kind == "care" else "several patients taking part in one program"
    if conversation.context_type == training_groups.LINK_TYPE:
        title = training_groups.context_names(db, [conversation]).get(conversation.id)
        kind_text = f"the learners of the training \"{title}\"" if title else "the learners of one training"
    return {
        "conversation_id": conversation.id, "name": conversation.subject or "", "kind": conversation.group_kind or "program",
        "description": conversation.description,
        "purpose": f"This is a {conversation.group_kind} group for {kind_text}.",
        "invited_by_name": inviter_name, "provider_names": provider_names,
        "member_count": len(active), "visibility_notice": common.visibility_notice(len(active)),
        "what_members_can_see": (
            "Everyone in the group can read every message and file posted after they join. "
            + ("Members of the care team are named in full. " if conversation.group_kind == "care"
               else "Patients see each other by display name only; no phone numbers or profile details are shared. ")
            + "You can leave at any time."
        ),
        "emergency_notice": common.emergency_notice(), "requires_guardian_consent": bool(member.is_minor),
        "invited_at": member.invited_at,
    }


def list_invitations(db: Session, current_user: dict) -> dict:
    user_id = common.user_uuid(current_user)
    rows = (
        db.query(Conversation, ConversationParticipant)
        .join(ConversationParticipant, ConversationParticipant.conversation_id == Conversation.id)
        .filter(
            Conversation.conversation_type == common.GROUP_TYPE, Conversation.is_deleted.is_(False),
            Conversation.status == "open",
            ConversationParticipant.user_id == user_id, ConversationParticipant.membership_status == "invited",
        )
        .order_by(ConversationParticipant.invited_at.desc()).all()
    )
    items = [_invitation_dict(db, c, m, current_user) for c, m in rows]
    return {"items": items, "total": len(items)}


def get_invitation(db: Session, current_user: dict, conversation_id: UUID) -> dict:
    conversation = common.load_group(db, conversation_id)
    member = common.get_member(db, conversation.id, common.user_uuid(current_user))
    if member is None or member.membership_status != "invited":
        raise HTTPException(status_code=404, detail="Invitation not found")
    return _invitation_dict(db, conversation, member, current_user)


def update_group(db: Session, current_user: dict, conversation_id: UUID, payload, access_token: str | None = None) -> dict:
    conversation = common.load_group(db, conversation_id)
    member = common.active_member(db, conversation, current_user)
    common.require_admin(member, "edit the group")
    common.assert_open(conversation)
    changes: dict = {}
    if payload.name is not None and " ".join(payload.name.split()) != conversation.subject:
        new_name = " ".join(payload.name.split())
        if not new_name:
            raise HTTPException(status_code=422, detail="A group needs a name")
        # patients already in the group and those invited (the inviter knows who they are)
        patients = (
            db.query(ConversationParticipant.user_id)
            .filter(ConversationParticipant.conversation_id == conversation.id, ConversationParticipant.member_kind == "patient",
                    ConversationParticipant.membership_status.in_(("active", "invited")))
            .all()
        )
        names = common.resolve_names(db, conversation, [row[0] for row in patients], current_user)
        common.assert_name_allowed(db, conversation.id, new_name, [n for n in names.values() if n])
        changes["name"] = {"from": conversation.subject, "to": new_name}
        conversation.subject = new_name
    if payload.description is not None and payload.description != conversation.description:
        changes["description"] = {"from": conversation.description, "to": payload.description}
        conversation.description = payload.description or None
    if payload.avatar_url is not None and payload.avatar_url != conversation.avatar_url:
        changes["avatar_url"] = {"from": conversation.avatar_url, "to": payload.avatar_url}
        conversation.avatar_url = payload.avatar_url or None
    if changes:
        common.audit(db, conversation, actor=current_user, action="group_updated", details=changes)
        names = common.resolve_names(db, conversation, [member.user_id], current_user)
        if "name" in changes:
            system_message(db, conversation, member.user_id, f"{short_name(member, names)} renamed the group")
        db.commit()
        notify.emit_group(conversation.id, "group_updated", {
            "name": conversation.subject, "description": conversation.description, "avatar_url": conversation.avatar_url,
        })
    return group_dict(db, conversation, member)


def update_settings(db: Session, current_user: dict, conversation_id: UUID, payload) -> dict:
    conversation = common.load_group(db, conversation_id)
    member = common.active_member(db, conversation, current_user)
    common.require_admin(member, "change the group settings")
    common.assert_open(conversation)
    if bool(conversation.admins_only_posting) != payload.admins_only_posting:
        common.audit(db, conversation, actor=current_user, action="settings_changed",
                     details={"admins_only_posting": {"from": bool(conversation.admins_only_posting), "to": payload.admins_only_posting}})
        conversation.admins_only_posting = payload.admins_only_posting
        names = common.resolve_names(db, conversation, [member.user_id], current_user)
        system_message(
            db, conversation, member.user_id,
            f"{short_name(member, names)} turned {'on' if payload.admins_only_posting else 'off'} posting by providers only",
        )
        db.commit()
        notify.emit_group(conversation.id, "group_updated", {"admins_only_posting": conversation.admins_only_posting})
    return group_dict(db, conversation, member)


# ---------------------------------------------------------------- members

def list_members(db: Session, current_user: dict, conversation_id: UUID) -> dict:
    conversation = common.load_group(db, conversation_id)
    viewer = common.active_member(db, conversation, current_user)
    statuses = ["active", "invited"] if common.is_admin(viewer) else ["active"]
    rows = (
        db.query(ConversationParticipant)
        .filter(ConversationParticipant.conversation_id == conversation.id, ConversationParticipant.membership_status.in_(statuses))
        .order_by(ConversationParticipant.joined_at.asc()).all()
    )
    full = common.resolve_names(db, conversation, [r.user_id for r in rows], current_user)
    # An owner or admin can tell who they invited: when the identity service has no name for a pending invitee, use the
    # name on their enrolment (training groups). Only admins see invited rows, and only an admin invited them.
    pending_names = (
        training_groups.enrolment_names(db, conversation, [r.user_id for r in rows if r.membership_status == "invited"])
        if common.is_admin(viewer) else {}
    )
    order = {"owner": 0, "admin": 1, "member": 2}
    rows.sort(key=lambda r: (r.membership_status != "active", order.get(r.group_role or "member", 2), r.joined_at or datetime.min))
    members = [
        {
            "user_id": r.user_id,
            "name": common.name_for_viewer(conversation, viewer, r, full) or (pending_names.get(r.user_id) if r.membership_status == "invited" else None),
            "role": r.group_role,
            "kind": r.member_kind, "status": r.membership_status, "is_me": r.user_id == viewer.user_id,
            "joined_at": r.joined_at if r.membership_status == "active" else None,
            "posting_muted": common.posting_muted_now(r) if common.is_admin(viewer) else False,
            "posting_muted_until": r.posting_muted_until if common.is_admin(viewer) else None,
        }
        for r in rows
    ]
    return {"members": members, "total": len(members)}


def invite_members(db: Session, current_user: dict, conversation_id: UUID, payload, access_token: str | None = None) -> dict:
    conversation = common.load_group(db, conversation_id)
    member = common.active_member(db, conversation, current_user)
    common.require_admin(member, "invite people")
    common.assert_open(conversation)
    unique = {i.user_id: i for i in payload.invitees}
    if conversation.context_type == training_groups.LINK_TYPE and conversation.context_id:
        training_groups.check_invitees(
            db, training_groups._load_training(db, conversation.context_id), conversation.tenant_id,
            [u for u in unique if u != member.user_id], access_token,
        )
    _capacity_check(db, conversation, len({uid for uid in unique if uid != member.user_id}))
    return _send_invitations(db, conversation, member, list(unique.values()), current_user)


def _send_invitations(db: Session, conversation: Conversation, member: ConversationParticipant, invitees, current_user: dict) -> dict:
    rows, skipped = _invitee_rows(db, conversation, invitees, member.user_id)
    for row in rows:
        common.audit(db, conversation, actor=current_user, action="member_invited", target_user_id=row.user_id,
                     details={"member_kind": row.member_kind, "is_minor": row.is_minor})
    db.commit()
    names = common.resolve_names(db, conversation, [member.user_id], current_user)
    notify.notify_invitation(db, conversation, [r.user_id for r in rows], names.get(str(member.user_id)))
    for row in rows:
        notify.emit_user(row.user_id, "group_invitation", {"conversation_id": str(conversation.id)})
    return {"invited": [r.user_id for r in rows], "skipped": skipped}


def invite_enrolled(db: Session, current_user: dict, conversation_id: UUID, payload, access_token: str | None = None) -> dict:
    """Training groups: invite chosen learners (and Providers) by id, or every eligible enrolled learner who is not in the
    group yet, up to the free places. A training with more learners than the group limit needs more than one group: the
    ones left out are counted in `not_invited_group_full`."""
    conversation = common.load_group(db, conversation_id)
    member = common.active_member(db, conversation, current_user)
    common.require_admin(member, "invite people")
    common.assert_open(conversation)
    if conversation.context_type != training_groups.LINK_TYPE or not conversation.context_id:
        raise HTTPException(status_code=422, detail="This works for groups linked to a training only")
    limit = common.max_members(conversation.group_kind)
    held = common.count_active(db, conversation.id) + common.count_pending(db, conversation.id)
    stats = {"no_account": 0, "not_invited_group_full": 0}
    if payload.all_eligible:
        training = training_groups._load_training(db, conversation.context_id)
        invitees, stats = training_groups.pick_eligible(
            db, conversation, training, member.user_id, access_token, limit=limit - held, exclude=set(payload.exclude_user_ids),
        )
    else:
        wanted = {u for u in payload.user_ids if u != member.user_id}
        training_groups.check_invitees(
            db, training_groups._load_training(db, conversation.context_id), conversation.tenant_id, wanted, access_token,
        )
        _capacity_check(db, conversation, len(wanted))
        invitees = [SimpleNamespace(user_id=u, display_name=None, is_minor=False) for u in wanted]
    result = _send_invitations(db, conversation, member, invitees, current_user) if invitees else {"invited": [], "skipped": []}
    free = limit - (common.count_active(db, conversation.id) + common.count_pending(db, conversation.id))
    return {**result, "no_account": stats["no_account"], "not_invited_group_full": stats["not_invited_group_full"], "free_places": max(free, 0)}


def accept_invitation(db: Session, current_user: dict, conversation_id: UUID, payload) -> dict:
    conversation = common.load_group(db, conversation_id)
    user_id = common.user_uuid(current_user)
    member = common.get_member(db, conversation.id, user_id)
    if member is None or member.membership_status != "invited":
        raise HTTPException(status_code=404, detail="Invitation not found")
    if not payload.consent:
        raise HTTPException(status_code=422, detail="Consent is required to join the group")
    if member.is_minor and payload.guardian is None:
        raise HTTPException(status_code=422, detail="A parent's or guardian's consent is required for someone under 18")
    common.assert_open(conversation)
    if common.count_active(db, conversation.id) >= common.max_members(conversation.group_kind):
        raise HTTPException(status_code=409, detail="This group is full")

    now = datetime.utcnow()
    notice = _invitation_dict(db, conversation, member, current_user)
    notice = {k: (str(v) if isinstance(v, (UUID, datetime)) else v) for k, v in notice.items()}
    full = common.resolve_names(db, conversation, [user_id], current_user)
    member.membership_status = "active"
    member.responded_at = now
    member.joined_at = now
    member.history_from = now  # nothing sent before this moment is visible to them
    member.display_name = (
        common.clean_display_name(payload.display_name) or member.display_name
        or (common.default_display_name(full.get(str(user_id))) if member.member_kind == "patient" else None)
    )
    db.add(GroupConsent(
        conversation_id=conversation.id, user_id=user_id, granted_at=now, is_minor=bool(member.is_minor),
        guardian_name=payload.guardian.name if payload.guardian else None,
        guardian_relationship=payload.guardian.relationship if payload.guardian else None, notice=notice,
    ))
    common.audit(db, conversation, actor=current_user, action="consent_granted", target_user_id=user_id,
                 details={"is_minor": bool(member.is_minor), "guardian_consent": payload.guardian is not None})
    names = common.resolve_names(db, conversation, [user_id, member.invited_by] if member.invited_by else [user_id], current_user)
    inviter_text = ""
    if member.invited_by:
        inviter = common.get_member(db, conversation.id, member.invited_by)
        if inviter is not None:
            inviter_text = f"{short_name(inviter, names)} added "
    system_message(db, conversation, user_id, f"{inviter_text}{short_name(member, names)}" if inviter_text else f"{short_name(member, names)} joined")
    db.commit()
    notify.sync_rooms(user_id, conversation.id, join=True)
    notify.emit_group(conversation.id, "member_joined", {"user_id": str(user_id), "role": "member", "kind": member.member_kind})
    return group_dict(db, conversation, member)


def decline_invitation(db: Session, current_user: dict, conversation_id: UUID, payload) -> dict:
    conversation = common.load_group(db, conversation_id)
    user_id = common.user_uuid(current_user)
    member = common.get_member(db, conversation.id, user_id)
    if member is None or member.membership_status != "invited":
        raise HTTPException(status_code=404, detail="Invitation not found")
    member.membership_status = "declined"
    member.responded_at = datetime.utcnow()
    common.audit(db, conversation, actor=current_user, action="consent_declined", target_user_id=user_id, reason=payload.reason if payload else None)
    db.commit()
    return {"conversation_id": conversation.id, "status": "declined"}


def _acting_on_members(db: Session, conversation: Conversation, current_user: dict, access_token: str | None, what: str):
    """(the caller's member row or None, is_compliance). A group admin acts as a member; the clinic's compliance admin or
    the platform may act on members without being in the group (decision 7: with a recorded reason for admins)."""
    member = common.get_member(db, conversation.id, common.user_uuid(current_user))
    member = member if member is not None and member.membership_status == "active" else None
    compliance = common.is_compliance_admin(db, current_user, conversation, access_token)
    if member is None and not compliance:
        raise HTTPException(status_code=403, detail="You are not a member of this group")
    if not compliance:
        common.require_admin(member, what)
    return member, compliance


def remove_member(db: Session, current_user: dict, conversation_id: UUID, target_id: UUID, reason: str | None, access_token: str | None = None) -> dict:
    conversation = common.load_group(db, conversation_id)
    caller, compliance = _acting_on_members(db, conversation, current_user, access_token, "remove people")
    actor_id = common.user_uuid(current_user)
    caller_role = caller.group_role if caller is not None else None
    if target_id == actor_id:
        raise HTTPException(status_code=422, detail="Use leave to leave the group yourself")
    target = common.get_member(db, conversation.id, target_id)
    if target is None or target.membership_status not in ("active", "invited"):
        raise HTTPException(status_code=404, detail="Member not found")
    if target.group_role == "owner":
        raise HTTPException(status_code=403, detail="The owner cannot be removed. Hand the group over or ask compliance to reassign it")
    if target.group_role == "admin" and caller_role != "owner" and not compliance:
        raise HTTPException(status_code=403, detail="Only the owner or compliance can remove an admin")
    if target.group_role == "admin" and not (reason and reason.strip()):
        raise HTTPException(status_code=422, detail="A reason is required to remove an admin")

    was_active = target.membership_status == "active"
    now = datetime.utcnow()
    target.membership_status = "removed"
    target.removed_by, target.removed_reason, target.left_at = actor_id, (reason or None), now
    target.group_role = "member" if target.group_role == "admin" else target.group_role
    common.audit(db, conversation, actor=current_user, action="member_removed" if was_active else "invitation_cancelled",
                 target_user_id=target_id, reason=reason)
    if was_active:
        names = common.resolve_names(db, conversation, [target_id], current_user)
        system_message(db, conversation, actor_id, f"{short_name(target, names)} was removed")
    db.commit()
    if was_active:
        notify.notify_removed(db, conversation, target_id)
        notify.emit_user(target_id, "member_removed", {"conversation_id": str(conversation.id), "user_id": str(target_id)})
        notify.emit_group(conversation.id, "member_removed", {"user_id": str(target_id)})
        notify.sync_rooms(target_id, conversation.id, join=False)  # stops receiving messages straight away
    return {"conversation_id": conversation.id, "user_id": target_id, "status": "removed"}


def change_role(db: Session, current_user: dict, conversation_id: UUID, target_id: UUID, payload, access_token: str | None = None) -> dict:
    conversation = common.load_group(db, conversation_id)
    caller, compliance = _acting_on_members(db, conversation, current_user, access_token, "change roles")
    actor_id = common.user_uuid(current_user)
    caller_role = caller.group_role if caller is not None else None
    target = common.get_member(db, conversation.id, target_id)
    if target is None or target.membership_status != "active":
        raise HTTPException(status_code=404, detail="Member not found")
    if target.group_role == "owner":
        raise HTTPException(status_code=403, detail="The owner's role changes only by handing the group over")
    previous = target.group_role
    if payload.role == previous:
        return {"conversation_id": conversation.id, "user_id": target_id, "role": previous}
    if payload.role == "admin":
        if target.member_kind != "provider" or not common.is_verified_provider(db, target_id, conversation.tenant_id):
            raise HTTPException(status_code=422, detail="Only a verified provider can be an admin; patients cannot")
        if common.count_admins(db, conversation.id) >= settings.GROUP_MAX_ADMINS:
            raise HTTPException(status_code=409, detail=f"A group can have at most {settings.GROUP_MAX_ADMINS} admins")
    else:  # demoting an admin
        if caller_role != "owner" and not compliance:
            raise HTTPException(status_code=403, detail="Only the owner or compliance can change an admin")
        if not (payload.reason and payload.reason.strip()):
            raise HTTPException(status_code=422, detail="A reason is required to change an admin")
    target.group_role = payload.role
    common.audit(db, conversation, actor=current_user, action="role_changed", target_user_id=target_id, reason=payload.reason,
                 details={"from": previous, "to": payload.role})
    names = common.resolve_names(db, conversation, [target_id, actor_id], current_user)
    who = short_name(caller, names) if caller is not None else "Compliance"
    system_message(db, conversation, actor_id,
                   f"{who} made {short_name(target, names)} an admin" if payload.role == "admin"
                   else f"{short_name(target, names)} is no longer an admin")
    db.commit()
    notify.notify_role_changed(db, conversation, target_id, payload.role)
    notify.emit_group(conversation.id, "member_role_changed", {"user_id": str(target_id), "role": payload.role})
    return {"conversation_id": conversation.id, "user_id": target_id, "role": payload.role}


def leave_group(db: Session, current_user: dict, conversation_id: UUID) -> dict:
    conversation = common.load_group(db, conversation_id)
    member = common.active_member(db, conversation, current_user)
    if member.group_role == "owner":
        raise HTTPException(status_code=409, detail="The owner must hand the group over to another verified provider before leaving")
    _deactivate(db, conversation, member, current_user, status="left", action="member_left")
    return {"conversation_id": conversation.id, "status": "left"}


def withdraw_consent(db: Session, current_user: dict, conversation_id: UUID) -> dict:
    """Take consent back: the same as leaving, and the consent record shows it was withdrawn."""
    conversation = common.load_group(db, conversation_id)
    user_id = common.user_uuid(current_user)
    member = common.get_member(db, conversation.id, user_id)
    if member is None or member.membership_status not in ("active", "invited"):
        raise HTTPException(status_code=404, detail="You have no active consent for this group")
    if member.group_role == "owner":
        raise HTTPException(status_code=409, detail="The owner must hand the group over before withdrawing")
    consent = (
        db.query(GroupConsent).filter(GroupConsent.conversation_id == conversation.id, GroupConsent.user_id == user_id,
                                      GroupConsent.withdrawn_at.is_(None)).order_by(GroupConsent.granted_at.desc()).first()
    )
    if consent is not None:
        consent.withdrawn_at = datetime.utcnow()
    if member.membership_status == "invited":
        member.membership_status, member.responded_at = "declined", datetime.utcnow()
        common.audit(db, conversation, actor=current_user, action="consent_declined", target_user_id=user_id)
        db.commit()
    else:
        _deactivate(db, conversation, member, current_user, status="left", action="consent_withdrawn")
    return {"conversation_id": conversation.id, "status": "withdrawn"}


def _deactivate(db, conversation, member, current_user, *, status: str, action: str) -> None:
    names = common.resolve_names(db, conversation, [member.user_id], current_user)
    member.membership_status, member.left_at = status, datetime.utcnow()
    common.audit(db, conversation, actor=current_user, action=action, target_user_id=member.user_id)
    system_message(db, conversation, member.user_id, f"{short_name(member, names)} left")
    db.commit()
    notify.emit_group(conversation.id, "member_left", {"user_id": str(member.user_id)})
    notify.sync_rooms(member.user_id, conversation.id, join=False)


def _verified_provider_target(db: Session, conversation: Conversation, target_id: UUID) -> ConversationParticipant:
    target = common.get_member(db, conversation.id, target_id)
    if target is None or target.membership_status != "active":
        raise HTTPException(status_code=404, detail="Member not found")
    if target.member_kind != "provider" or not common.is_verified_provider(db, target_id, conversation.tenant_id):
        raise HTTPException(status_code=422, detail="Only a verified provider can own a group; patients never can")
    return target


def _make_owner(db, conversation, new_owner: ConversationParticipant, old_owner: ConversationParticipant | None) -> None:
    if old_owner is not None and old_owner.user_id != new_owner.user_id:
        old_owner.group_role = "admin" if common.count_admins(db, conversation.id) < settings.GROUP_MAX_ADMINS else "member"
    new_owner.group_role = "owner"


def transfer_ownership(db: Session, current_user: dict, conversation_id: UUID, payload) -> dict:
    conversation = common.load_group(db, conversation_id)
    caller = common.active_member(db, conversation, current_user)
    if caller.group_role != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can hand the group over")
    if payload.user_id == caller.user_id:
        raise HTTPException(status_code=422, detail="Choose another member")
    target = _verified_provider_target(db, conversation, payload.user_id)
    _make_owner(db, conversation, target, caller)
    common.audit(db, conversation, actor=current_user, action="ownership_transferred", target_user_id=target.user_id,
                 details={"from": str(caller.user_id), "to": str(target.user_id)})
    db.commit()
    notify.notify_role_changed(db, conversation, target.user_id, "owner")
    notify.emit_group(conversation.id, "member_role_changed", {"user_id": str(target.user_id), "role": "owner"})
    return {"conversation_id": conversation.id, "owner_id": target.user_id}


def reassign_owner(db: Session, current_user: dict, conversation_id: UUID, payload, access_token: str | None = None) -> dict:
    """Compliance (the clinic's Enterprise Admin or the platform) names a new owner, with a recorded reason, e.g. when
    the owner cannot be reached."""
    conversation = common.load_group(db, conversation_id)
    if not common.is_compliance_admin(db, current_user, conversation, access_token):
        raise HTTPException(status_code=403, detail="Only the clinic's compliance admin or the platform can reassign a group")
    target = _verified_provider_target(db, conversation, payload.user_id)
    old = (
        db.query(ConversationParticipant)
        .filter(ConversationParticipant.conversation_id == conversation.id, ConversationParticipant.group_role == "owner",
                ConversationParticipant.membership_status == "active").first()
    )
    _make_owner(db, conversation, target, old)
    common.audit(db, conversation, actor=current_user, action="owner_reassigned", target_user_id=target.user_id, reason=payload.reason,
                 details={"from": str(old.user_id) if old else None, "to": str(target.user_id)})
    db.commit()
    notify.notify_role_changed(db, conversation, target.user_id, "owner")
    notify.emit_group(conversation.id, "member_role_changed", {"user_id": str(target.user_id), "role": "owner"})
    return {"conversation_id": conversation.id, "owner_id": target.user_id}


def mute_posting(db: Session, current_user: dict, conversation_id: UUID, target_id: UUID, payload) -> dict:
    conversation = common.load_group(db, conversation_id)
    caller = common.active_member(db, conversation, current_user)
    common.require_admin(caller, "mute someone")
    target = common.get_member(db, conversation.id, target_id)
    if target is None or target.membership_status != "active":
        raise HTTPException(status_code=404, detail="Member not found")
    if target.group_role == "owner" or (target.group_role == "admin" and caller.group_role != "owner"):
        raise HTTPException(status_code=403, detail="You cannot mute the owner or another admin")
    if payload.until is not None and payload.until <= datetime.utcnow():
        raise HTTPException(status_code=422, detail="The end of the mute must be in the future")
    target.posting_muted, target.posting_muted_until = True, payload.until
    target.posting_muted_by, target.posting_muted_reason = caller.user_id, payload.reason
    common.audit(db, conversation, actor=current_user, action="posting_muted", target_user_id=target_id, reason=payload.reason,
                 details={"until": payload.until.isoformat() if payload.until else None})
    db.commit()
    notify.emit_user(target_id, "posting_muted", {"conversation_id": str(conversation.id), "until": payload.until})
    return {"conversation_id": conversation.id, "user_id": target_id, "posting_muted": True, "until": payload.until}


def unmute_posting(db: Session, current_user: dict, conversation_id: UUID, target_id: UUID) -> dict:
    conversation = common.load_group(db, conversation_id)
    caller = common.active_member(db, conversation, current_user)
    common.require_admin(caller, "unmute someone")
    target = common.get_member(db, conversation.id, target_id)
    if target is None or target.membership_status != "active":
        raise HTTPException(status_code=404, detail="Member not found")
    if target.posting_muted:
        target.posting_muted, target.posting_muted_until, target.posting_muted_by, target.posting_muted_reason = False, None, None, None
        common.audit(db, conversation, actor=current_user, action="posting_unmuted", target_user_id=target_id)
        db.commit()
        notify.emit_user(target_id, "posting_unmuted", {"conversation_id": str(conversation.id)})
    return {"conversation_id": conversation.id, "user_id": target_id, "posting_muted": False}


# ---------------------------------------------------------------- closing

def close_group(db: Session, current_user: dict, conversation_id: UUID, payload) -> dict:
    """The program or care episode ended: the group becomes read-only and the retention period starts."""
    conversation = common.load_group(db, conversation_id)
    caller = common.active_member(db, conversation, current_user)
    common.require_admin(caller, "close the group")
    if conversation.status == "closed":
        raise HTTPException(status_code=409, detail="The group is already closed")
    now = datetime.utcnow()
    conversation.status, conversation.is_read_only = "closed", True
    conversation.closed_at, conversation.closed_reason = now, (payload.reason if payload else None)
    conversation.retention_started_at = now
    common.audit(db, conversation, actor=current_user, action="group_closed", reason=payload.reason if payload else None,
                 details={"retention_started_at": now.isoformat()})
    names = common.resolve_names(db, conversation, [caller.user_id], current_user)
    system_message(db, conversation, caller.user_id, f"{short_name(caller, names)} closed the group. It is now read-only")
    db.commit()
    members = [m.user_id for m in common.active_members(db, conversation.id)]
    notify.notify_closed(db, conversation, members)
    notify.emit_group(conversation.id, "group_closed", {"closed_at": now})
    return group_dict(db, conversation, caller)


def reopen_group(db: Session, current_user: dict, conversation_id: UUID, payload, access_token: str | None = None) -> dict:
    """Reopening needs the owner (or compliance) and a reason; it stops the retention clock."""
    conversation = common.load_group(db, conversation_id)
    member = common.get_member(db, conversation.id, common.user_uuid(current_user))
    is_owner = member is not None and member.membership_status == "active" and member.group_role == "owner"
    if not is_owner and not common.is_compliance_admin(db, current_user, conversation, access_token):
        raise HTTPException(status_code=403, detail="Only the owner can reopen a closed group")
    if conversation.status != "closed":
        raise HTTPException(status_code=409, detail="The group is not closed")
    conversation.status, conversation.is_read_only = "open", False
    conversation.closed_at = conversation.retention_started_at = None
    common.audit(db, conversation, actor=current_user, action="group_reopened", reason=payload.reason)
    system_message(db, conversation, common.user_uuid(current_user), "The group was reopened")
    db.commit()
    notify.notify_closed(db, conversation, [m.user_id for m in common.active_members(db, conversation.id)], reopened=True)
    notify.emit_group(conversation.id, "group_reopened", {})
    return group_dict(db, conversation, member if member is not None and member.membership_status == "active" else None)


# ---------------------------------------------------------------- the member's own list

def update_preferences(db: Session, current_user: dict, conversation_id: UUID, payload) -> dict:
    """Mute (never removes access), pin. Archive is the existing PATCH /conversations/{id}/archive."""
    conversation = common.load_group(db, conversation_id)
    member = common.active_member(db, conversation, current_user)
    if payload.muted is not None:
        member.muted = payload.muted
        member.muted_until = payload.muted_until if payload.muted else None
    elif payload.muted_until is not None:
        member.muted_until = payload.muted_until
    if payload.pinned is not None:
        member.pinned = payload.pinned
    db.commit()
    return group_dict(db, conversation, member)
