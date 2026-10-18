"""Shared rules for group chat: who someone is in a group, what they may do, the audit trail, and how names are shown.

Vocabulary used throughout (docs/group-chat-api.md):

* membership_status of a participant row: invited -> active -> (left | removed); or invited -> declined. Only an
  `active` member can read or write anything. A person who has not accepted sees no messages at all.
* group_role: owner (the provider who created the group), admin, member. An owner is also an admin for permissions.
* member_kind: provider or patient. Only a provider can be an admin or owner, and only providers are named in full to
  patients in a program group.
* compliance admin: an Enterprise Admin (the clinic) of the group's business, or an active Platform Super Admin.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.chat_model import Conversation, ConversationParticipant
from app.models.group_chat_model import GroupAuditLog, VerifiedProvider

logger = logging.getLogger(__name__)

GROUP_TYPE = "group"
LINK_TYPES = ("program", "appointment_series", "care_episode")
ADMIN_ROLES = ("owner", "admin")


# ---------------------------------------------------------------- who is calling

def user_uuid(current_user: dict) -> UUID:
    try:
        return UUID(str(current_user["id"]))
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid user identity")


def platform_role(current_user: dict) -> str:
    return str((current_user or {}).get("role") or "").lower()


def is_super_admin(current_user: dict) -> bool:
    """An active Platform Super Admin."""
    from app.services.super_admin_identity import profile_status_is_active

    return platform_role(current_user) == "super_admin" and profile_status_is_active(current_user)


def caller_tenant(db: Session, current_user: dict, access_token: str | None) -> UUID | None:
    from app.core.auth_context import resolve_auth_tenant_id_with_db

    raw = resolve_auth_tenant_id_with_db(db, current_user, access_token=access_token)
    try:
        return UUID(str(raw)) if raw else None
    except (TypeError, ValueError):
        return None


def is_compliance_admin(db: Session, current_user: dict, conversation: Conversation, access_token: str | None) -> bool:
    """The clinic's compliance side (an Enterprise Admin of the group's business) or the platform's (Super Admin)."""
    if is_super_admin(current_user):
        return True
    if platform_role(current_user) != "admin" or conversation.tenant_id is None:
        return False
    return caller_tenant(db, current_user, access_token) == conversation.tenant_id


def is_verified_provider(db: Session, user_id: UUID, tenant_id: UUID | None) -> bool:
    """Whether the provider has a current verification for this clinic.

    Provider registrations are clinic-scoped.  In particular, a record created by
    a Super Admin for one clinic must never authorize the same person to administer
    a group belonging to another clinic.
    """
    if tenant_id is None:
        return False
    return (
        db.query(VerifiedProvider.id)
        .filter(
            VerifiedProvider.user_id == user_id,
            VerifiedProvider.tenant_id == tenant_id,
            VerifiedProvider.status == "verified",
        )
        .first()
        is not None
    )


def assert_can_create_groups(db: Session, current_user: dict, tenant_id: UUID | None) -> None:
    """Verified providers and clinic admins only. Patients can never create a group."""
    role = platform_role(current_user)
    if role == "admin":
        return  # the clinic (Enterprise Admin)
    if role == "provider":
        if not settings.GROUP_REQUIRE_VERIFIED_PROVIDER or is_verified_provider(db, user_uuid(current_user), tenant_id):
            return
        raise HTTPException(
            status_code=403,
            detail="Your registration has not been verified yet. Only verified providers can create a group.",
        )
    raise HTTPException(status_code=403, detail="Only verified providers and clinic admins can create a group")


# ---------------------------------------------------------------- the group and its members

def load_group(db: Session, conversation_id: UUID) -> Conversation:
    conversation = (
        db.query(Conversation)
        .filter(Conversation.id == conversation_id, Conversation.is_deleted.is_(False))
        .first()
    )
    if conversation is None or conversation.conversation_type != GROUP_TYPE:
        raise HTTPException(status_code=404, detail="Group not found")
    return conversation


def get_member(db: Session, conversation_id: UUID, user_id: UUID) -> ConversationParticipant | None:
    """The participant row whatever its status (invited, removed ...)."""
    return (
        db.query(ConversationParticipant)
        .filter(ConversationParticipant.conversation_id == conversation_id, ConversationParticipant.user_id == user_id)
        .first()
    )


def active_member(db: Session, conversation: Conversation, current_user: dict) -> ConversationParticipant:
    """403 unless the caller is an active member of this group. Someone invited but not yet accepted, removed, or
    who left is not a member and gets the same answer as a stranger."""
    member = get_member(db, conversation.id, user_uuid(current_user))
    if member is None or member.membership_status != "active":
        raise HTTPException(status_code=403, detail="You are not a member of this group")
    return member


def active_members(db: Session, conversation_id: UUID) -> list[ConversationParticipant]:
    return (
        db.query(ConversationParticipant)
        .filter(
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.membership_status == "active",
        )
        .order_by(ConversationParticipant.joined_at.asc())
        .all()
    )


def count_active(db: Session, conversation_id: UUID) -> int:
    return (
        db.query(ConversationParticipant.id)
        .filter(
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.membership_status == "active",
        )
        .count()
    )


def count_pending(db: Session, conversation_id: UUID) -> int:
    return (
        db.query(ConversationParticipant.id)
        .filter(
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.membership_status == "invited",
        )
        .count()
    )


def count_admins(db: Session, conversation_id: UUID) -> int:
    return (
        db.query(ConversationParticipant.id)
        .filter(
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.membership_status == "active",
            ConversationParticipant.group_role == "admin",
        )
        .count()
    )


def is_admin(member: ConversationParticipant) -> bool:
    return member.membership_status == "active" and member.group_role in ADMIN_ROLES


def require_admin(member: ConversationParticipant, what: str = "do this") -> None:
    if not is_admin(member):
        raise HTTPException(status_code=403, detail=f"Only the group owner or an admin can {what}")


def max_members(kind: str | None) -> int:
    return settings.GROUP_MAX_MEMBERS_CARE if kind == "care" else settings.GROUP_MAX_MEMBERS_PROGRAM


def assert_open(conversation: Conversation) -> None:
    if conversation.status != "open" or conversation.is_read_only:
        raise HTTPException(status_code=409, detail="This group is closed (read-only)")


def retention_until(conversation: Conversation) -> datetime | None:
    if conversation.retention_started_at is None or settings.GROUP_RETENTION_DAYS is None:
        return None
    return conversation.retention_started_at + timedelta(days=settings.GROUP_RETENTION_DAYS)


def visibility_notice(active_count: int) -> str:
    return f"{active_count} {'person' if active_count == 1 else 'people'} can see this"


def emergency_notice() -> dict:
    number = settings.GROUP_EMERGENCY_NUMBER
    return {"text": f"Not for emergencies. In an emergency call {number}.", "number": number}


def member_is_muted_now(member: ConversationParticipant, now: datetime | None = None) -> bool:
    """The member's own notification mute (never affects access)."""
    now = now or datetime.utcnow()
    return bool(member.muted and (member.muted_until is None or member.muted_until > now))


def posting_muted_now(member: ConversationParticipant, now: datetime | None = None) -> bool:
    """An admin muted them: they can read but not post, until posting_muted_until if set."""
    now = now or datetime.utcnow()
    return bool(member.posting_muted and (member.posting_muted_until is None or member.posting_muted_until > now))


# ---------------------------------------------------------------- the audit trail (insert only)

def audit(
    db: Session, conversation: Conversation, *, actor: dict | None, action: str, target_user_id=None, message_id=None,
    reason: str | None = None, details: dict | None = None,
) -> None:
    """Add an audit row to the current transaction; the caller commits, so the row and the change are saved together.
    The trail is read-only for everyone: this is the only writer and nothing updates or deletes a row."""
    try:
        actor_id = UUID(str(actor["id"])) if actor and actor.get("id") else None
    except (TypeError, ValueError):
        actor_id = None
    db.add(GroupAuditLog(
        conversation_id=conversation.id, tenant_id=conversation.tenant_id, actor_id=actor_id,
        actor_role=(platform_role(actor) or None) if actor else "system", action=action,
        target_user_id=target_user_id, message_id=message_id, reason=(reason or None) and reason[:500],
        details=details,
    ))


# ---------------------------------------------------------------- names (privacy)

def resolve_names(db: Session, conversation: Conversation, user_ids, current_user: dict | None = None) -> dict[str, str | None]:
    from app.services import chat_user_names

    try:
        return chat_user_names.resolve_display_names(
            list(user_ids), tenant_ids=[conversation.tenant_id], current_user=current_user, db=db
        )
    except Exception:
        logger.exception("could not resolve group member names")
        return {}


def name_for_viewer(
    conversation: Conversation, viewer: ConversationParticipant, target: ConversationParticipant,
    full_names: dict[str, str | None],
) -> str | None:
    """How `viewer` sees `target`. Providers see full names. In a program group a patient sees other patients by their
    display name only (never a real name, phone or profile detail); the providers running it are named in full."""
    full = full_names.get(str(target.user_id))
    if target.user_id == viewer.user_id:
        return full or target.display_name
    if conversation.group_kind == "program" and viewer.member_kind == "patient" and target.member_kind == "patient":
        return target.display_name or "Member"
    return full or target.display_name


def clean_display_name(value: str | None) -> str | None:
    text = " ".join(str(value).split()) if value else ""
    return text[:120] if text and "@" not in text else None  # an e-mail address is not a display name


def default_display_name(full_name: str | None) -> str | None:
    """"Priya Sharma" -> "Priya S." : first name and the initial of the last, so a program group never shows a full name."""
    parts = (full_name or "").split()
    if not parts or "@" in full_name:
        return None
    return parts[0] if len(parts) == 1 else f"{parts[0]} {parts[-1][0]}."


# ---------------------------------------------------------------- group names must not identify a patient

def _words(text: str) -> list[str]:
    import re

    return re.findall(r"[a-z0-9]+", (text or "").lower())  # "Priya's" -> priya, s


def assert_name_allowed(db: Session, conversation_id, name: str, patient_names: list[str]) -> None:
    """A group name must not include a patient's name or a health condition. Checked against the names of the people
    in (or being invited to) the group and the configured list of blocked terms, as whole words; it is a guard, not a
    guarantee, so the people who create groups are told the rule too."""
    import re

    lowered = " ".join(_words(name))
    for term in settings.group_blocked_name_terms:
        if term and re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", lowered):
            raise HTTPException(status_code=422, detail="The group name must not include a health condition")
    name_words = set(_words(name))
    for full in patient_names:
        for token in _words(full):
            if len(token) >= 3 and token in name_words:
                raise HTTPException(status_code=422, detail="The group name must not include a patient's name")
