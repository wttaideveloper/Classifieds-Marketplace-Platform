"""Group chat messages: sending, listing, editing, retracting, hiding, pinning, reactions, read status, search, files.

Nothing is ever permanently deleted. An edit keeps the original (`edit_history`). A retract (by the author) or a hide
(by an admin) only takes the message out of the group: it is shown as "Message retracted" / "Message removed by admin",
and the original stays in the secure record, readable only by the owner and compliance (every read is audited).

A person only ever sees messages sent after they joined (`history_from`). A removed or non-accepted member sees none.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.chat_model import ChatAttachment, Conversation, ConversationParticipant, Message, MessageReadReceipt
from app.models.group_chat_model import ClinicalRecordEntry, MessageReaction
from app.repository import chat_repo
from app.services import group_chat_common as common
from app.services import group_chat_notify as notify

logger = logging.getLogger(__name__)

PLACEHOLDERS = {"retracted": "Message retracted", "admin": "Message removed by admin"}
STATUS_BY_HIDDEN = {None: "visible", "retracted": "retracted", "admin": "removed_by_admin"}


# ---------------------------------------------------------------- who may post

def assert_can_post(conversation: Conversation, member: ConversationParticipant) -> None:
    common.assert_open(conversation)
    if common.posting_muted_now(member):
        raise HTTPException(status_code=403, detail="An admin has muted you in this group. You can read but not post")
    provider = member.member_kind == "provider"
    if conversation.group_kind == "program" and not provider and not settings.GROUP_PROGRAM_PATIENTS_CAN_POST:
        raise HTTPException(status_code=403, detail="Only providers can post in a program group")
    if conversation.admins_only_posting and not provider:
        raise HTTPException(status_code=403, detail="Only providers can post in this group right now")


# ---------------------------------------------------------------- sending

def _validated_message(db: Session, conversation: Conversation, member: ConversationParticipant, payload) -> Message:
    assert_can_post(conversation, member)
    if payload.message_type == "text" and not (payload.content and payload.content.strip()) and not payload.attachment_id:
        raise HTTPException(status_code=422, detail="A message needs text or an attachment")
    if payload.attachment_id:
        attachment = chat_repo.get_attachment_by_id(db, payload.attachment_id)
        if not attachment or attachment.conversation_id != conversation.id:
            raise HTTPException(status_code=400, detail="Invalid attachment for this group")

    reply_to = None
    if payload.reply_to_id:
        reply_to = chat_repo.get_message_by_id(db, payload.reply_to_id)
        if reply_to is None or reply_to.conversation_id != conversation.id or reply_to.is_deleted:
            raise HTTPException(status_code=404, detail="The message you are replying to was not found in this group")
        if reply_to.hidden_kind or (member.history_from and reply_to.created_at <= member.history_from):
            raise HTTPException(status_code=400, detail="You cannot reply to that message")

    mentions: list[str] = []
    if payload.mentions:
        active_ids = {m.user_id for m in common.active_members(db, conversation.id)}
        for uid in dict.fromkeys(payload.mentions):
            if uid not in active_ids:
                raise HTTPException(status_code=422, detail="You can only tag people who are in this group")
            if uid != member.user_id:
                mentions.append(str(uid))
    if payload.mention_all and member.member_kind != "provider":
        raise HTTPException(status_code=403, detail="Only providers can tag everyone")

    return Message(
        conversation_id=conversation.id, sender_id=member.user_id, content=(payload.content or "").strip() or None,
        message_type=payload.message_type, attachment_id=payload.attachment_id,
        reply_to_id=reply_to.id if reply_to else None, mentions=mentions or None, mentions_all=bool(payload.mention_all),
    )


def _persist(db: Session, conversation: Conversation, message: Message) -> Message:
    db.add(message)
    db.flush()
    if message.attachment_id:
        attachment = chat_repo.get_attachment_by_id(db, message.attachment_id)
        if attachment:
            attachment.message_id = message.id
    conversation.last_message_at = message.created_at
    conversation.last_message_preview = chat_repo.message_list_preview(message)
    conversation.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(message)
    return message


def create_message(db: Session, current_user: dict, conversation_id: UUID, payload) -> tuple[Conversation, ConversationParticipant, Message]:
    """Validate, save and notify. Used by the REST endpoint and by the generic send path the socket uses."""
    conversation = common.load_group(db, conversation_id)
    member = common.active_member(db, conversation, current_user)
    message = _persist(db, conversation, _validated_message(db, conversation, member, payload))
    notify.notify_new_message(db, conversation, message, member.user_id, common.active_members(db, conversation.id))
    return conversation, member, message


def neutral_dict(message: Message) -> dict:
    """The same for every reader, for the real-time `new_message` event (names come from the member list)."""
    return {
        "id": message.id, "conversation_id": message.conversation_id, "sender_id": message.sender_id,
        "content": message.content, "message_type": message.message_type, "attachment_id": message.attachment_id,
        "reply_to_id": message.reply_to_id, "mentions": list(message.mentions or []), "mentions_all": bool(message.mentions_all),
        "is_deleted": False, "is_edited": False, "edited_at": None, "created_at": message.created_at, "read_by": [],
    }


def send_message(db: Session, current_user: dict, conversation_id: UUID, payload) -> dict:
    conversation, member, message = create_message(db, current_user, conversation_id, payload)
    notify.emit_group(conversation.id, "new_message", {"message": neutral_dict(message)})
    return render_one(db, conversation, member, message)


def mark_read(db: Session, current_user: dict, conversation_id: UUID) -> dict:
    """Mark the group read for the caller and tell the people who may see read receipts (`group_read`)."""
    from app.services.chat_service import mark_conversation_read_service

    conversation = common.load_group(db, conversation_id)         # 404 for anything that is not a group
    result = mark_conversation_read_service(db, current_user, conversation.id)
    reader = common.user_uuid(current_user)
    stored = chat_repo.get_participant(db, conversation.id, reader)
    if stored is not None and stored.last_read_at is not None:
        result = {**result, "read_at": stored.last_read_at}         # the time that was saved, so the event and the counts agree to the microsecond
    notify.emit_read(db, conversation, reader, result["read_at"])
    return result


def send_via_generic_path(db: Session, current_user: dict, data) -> dict:
    """POST /messages and the socket `send_message` event reach a group through here. The socket handler broadcasts
    the returned dict itself, so this does not emit."""
    _, _, message = create_message(db, current_user, data.conversation_id, data)
    return neutral_dict(message)


# ---------------------------------------------------------------- rendering (the viewer decides what is shown)

def _reactions_for(db: Session, message_ids: list[UUID], viewer_id: UUID):
    counts: dict[UUID, dict[str, int]] = {}
    mine: dict[UUID, list[str]] = {}
    if message_ids:
        for r in db.query(MessageReaction).filter(MessageReaction.message_id.in_(message_ids)).all():
            counts.setdefault(r.message_id, {})
            counts[r.message_id][r.emoji] = counts[r.message_id].get(r.emoji, 0) + 1
            if r.user_id == viewer_id:
                mine.setdefault(r.message_id, []).append(r.emoji)
    return counts, mine


def _seen_by(conversation: Conversation, message: Message, members: list[ConversationParticipant], receipts: dict[UUID, set[UUID]]) -> tuple[list[UUID], int]:
    recipients = [
        m for m in members
        if m.user_id != message.sender_id and (m.history_from is None or m.history_from < message.created_at)
    ]
    seen = [
        m.user_id for m in recipients
        if (m.last_read_at is not None and m.last_read_at >= message.created_at) or m.user_id in receipts.get(message.id, set())
    ]
    return seen, len(recipients)


def _unavailable(reason: str) -> dict:
    return {"available": False, "read_count": None, "recipient_count": None, "all_read": None, "reason": reason}


def _read_summary(conversation: Conversation, viewer: ConversationParticipant, message: Message, members: list[ConversationParticipant],
                  receipts: dict[UUID, set[UUID]]) -> dict:
    """Counts for one message, for the person who sent it. The same visibility rules as the read-status endpoint: in a program
    group only providers see read information."""
    if message.message_type == "system":
        return _unavailable("system_message")
    if message.hidden_kind or message.is_deleted:
        return _unavailable("hidden")
    if conversation.group_kind == "program" and viewer.member_kind != "provider":
        return _unavailable("not_shown_to_patients")
    if message.sender_id != viewer.user_id:
        return _unavailable("not_your_message")
    seen, recipients = _seen_by(conversation, message, members, receipts)
    return {"available": True, "read_count": len(seen), "recipient_count": recipients, "all_read": recipients > 0 and len(seen) == recipients, "reason": None}


def render_messages(db: Session, conversation: Conversation, viewer: ConversationParticipant, messages: list[Message], current_user: dict | None = None) -> list[dict]:
    members = common.active_members(db, conversation.id)
    every = {m.user_id: m for m in db.query(ConversationParticipant).filter(
        ConversationParticipant.conversation_id == conversation.id).all()}
    full = common.resolve_names(db, conversation, list({m.sender_id for m in messages} | {v.user_id for v in every.values()}), current_user)
    ids = [m.id for m in messages]
    counts, mine = _reactions_for(db, ids, viewer.user_id)
    reply_ids = {m.reply_to_id for m in messages if m.reply_to_id}
    quoted = {q.id: q for q in db.query(Message).filter(Message.id.in_(reply_ids)).all()} if reply_ids else {}
    receipts: dict[UUID, set[UUID]] = {}
    show_readers = conversation.group_kind == "care"
    # one query for the whole page: receipts of the messages whose counts or reader list will be shown
    wanted = [m.id for m in messages if show_readers or (m.sender_id == viewer.user_id and viewer.member_kind == "provider")]
    if wanted:
        for r in db.query(MessageReadReceipt).filter(MessageReadReceipt.message_id.in_(wanted)).all():
            receipts.setdefault(r.message_id, set()).add(r.user_id)

    def name(uid):
        target = every.get(uid)
        return common.name_for_viewer(conversation, viewer, target, full) if target is not None else full.get(str(uid))

    out = []
    for m in messages:
        hidden = m.hidden_kind
        sender = every.get(m.sender_id)
        quote = None
        if m.reply_to_id and m.reply_to_id in quoted:
            q = quoted[m.reply_to_id]
            q_hidden = bool(q.hidden_kind or q.is_deleted)
            quote = {"id": q.id, "sender_name": name(q.sender_id), "is_hidden": q_hidden,
                     "preview": None if q_hidden else chat_repo.message_list_preview(q)[:120] if chat_repo.message_list_preview(q) else None}
        seen, recipient_count = _seen_by(conversation, m, members, receipts) if show_readers and m.message_type != "system" else ([], 0)
        out.append({
            "id": m.id, "conversation_id": m.conversation_id, "sender_id": m.sender_id, "sender_name": name(m.sender_id),
            "sender_kind": sender.member_kind if sender else None, "message_type": m.message_type,
            "content": None if hidden or m.is_deleted else m.content,
            "attachment_id": None if hidden or m.is_deleted else m.attachment_id,
            "status": STATUS_BY_HIDDEN.get(hidden, "visible"), "placeholder": PLACEHOLDERS.get(hidden),
            "reply_to": quote, "mentions": [UUID(x) for x in (m.mentions or [])], "mentions_all": bool(m.mentions_all),
            "mentions_me": bool(m.mentions_all and m.sender_id != viewer.user_id) or str(viewer.user_id) in (m.mentions or []),
            "is_edited": bool(m.is_edited), "edited_at": m.edited_at, "pinned": m.pinned_at is not None and not hidden,
            "reactions": counts.get(m.id, {}), "my_reactions": mine.get(m.id, []),
            "read_by": seen if show_readers else None, "read_summary": _read_summary(conversation, viewer, m, members, receipts),
            "created_at": m.created_at,
        })
    return out


def render_one(db: Session, conversation: Conversation, viewer: ConversationParticipant, message: Message, current_user: dict | None = None) -> dict:
    return render_messages(db, conversation, viewer, [message], current_user)[0]


# ---------------------------------------------------------------- listing

def _visible_since(member: ConversationParticipant) -> datetime | None:
    return member.history_from


def list_messages(db: Session, current_user: dict, conversation_id: UUID, *, cursor: str | None = None, limit: int = 50) -> dict:
    conversation = common.load_group(db, conversation_id)
    member = common.active_member(db, conversation, current_user)
    since = _visible_since(member)
    query = db.query(Message).filter(Message.conversation_id == conversation.id, Message.is_deleted.is_(False))
    if since is not None:
        query = query.filter(Message.created_at > since)
    if cursor:
        cursor_dt, cursor_id = chat_repo._parse_cursor(cursor)
        query = query.filter(or_(Message.created_at < cursor_dt, and_(Message.created_at == cursor_dt, Message.id < cursor_id)))
    items = query.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit + 1).all()
    has_more = len(items) > limit
    items = items[:limit]
    next_cursor = chat_repo._build_cursor(items[-1].created_at, items[-1].id) if has_more and items else None
    items.reverse()

    # the "new messages" marker sits above the first message from someone else that this person has not read yet
    unread_since = member.last_read_at or member.history_from
    first_unread = (
        db.query(Message.id)
        .filter(Message.conversation_id == conversation.id, Message.is_deleted.is_(False), Message.hidden_kind.is_(None),
                Message.sender_id != member.user_id, Message.message_type != "system",
                *( [Message.created_at > unread_since] if unread_since is not None else [] ),
                *( [Message.created_at > since] if since is not None else [] ))
        .order_by(Message.created_at.asc(), Message.id.asc()).first()
    )
    from app.services.group_chat_service import _unread_count

    pinned_query = db.query(Message.id).filter(Message.conversation_id == conversation.id, Message.pinned_at.isnot(None), Message.hidden_kind.is_(None))
    if since is not None:
        pinned_query = pinned_query.filter(Message.created_at > since)
    return {
        "items": render_messages(db, conversation, member, items, current_user), "has_more": has_more, "next_cursor": next_cursor,
        "unread_count": _unread_count(db, conversation, member), "first_unread_message_id": first_unread[0] if first_unread else None,
        "pinned_count": pinned_query.count(),
    }


def _message_in_group(db: Session, conversation: Conversation, member: ConversationParticipant, message_id: UUID) -> Message:
    message = chat_repo.get_message_by_id(db, message_id)
    if message is None or message.conversation_id != conversation.id or message.is_deleted:
        raise HTTPException(status_code=404, detail="Message not found")
    if member.history_from is not None and message.created_at <= member.history_from:
        raise HTTPException(status_code=404, detail="Message not found")  # from before they joined
    return message


# ---------------------------------------------------------------- edit / retract / hide

def edit_message(db: Session, current_user: dict, conversation_id: UUID, message_id: UUID, payload) -> dict:
    conversation = common.load_group(db, conversation_id)
    member = common.active_member(db, conversation, current_user)
    message = _message_in_group(db, conversation, member, message_id)
    common.assert_open(conversation)
    if message.sender_id != member.user_id:
        raise HTTPException(status_code=403, detail="You can only edit your own messages")
    if message.hidden_kind:
        raise HTTPException(status_code=409, detail="A retracted or removed message cannot be edited")
    if message.message_type != "text":
        raise HTTPException(status_code=400, detail="Only text messages can be edited")
    now = datetime.utcnow()
    if now - message.created_at > timedelta(minutes=settings.GROUP_EDIT_WINDOW_MINUTES):
        raise HTTPException(status_code=403, detail=f"A message can only be edited within {settings.GROUP_EDIT_WINDOW_MINUTES} minutes")
    new_text = payload.content.strip()
    if not new_text:
        raise HTTPException(status_code=422, detail="A message cannot be empty")
    if new_text == (message.content or ""):
        return render_one(db, conversation, member, message, current_user)
    # the original stays: every earlier version is kept in order, oldest first
    history = list(message.edit_history or [])
    history.append({"content": message.content, "edited_at": now.isoformat(), "edited_by": str(member.user_id)})
    message.edit_history = history
    message.content, message.is_edited, message.edited_at = new_text, True, now
    common.audit(db, conversation, actor=current_user, action="message_edited", message_id=message.id, target_user_id=message.sender_id,
                 details={"version": len(history)})
    latest = chat_repo.get_latest_conversation_message(db, conversation.id)
    if latest is not None and latest.id == message.id:
        conversation.last_message_preview = new_text[:500]
    db.commit()
    db.refresh(message)
    rendered = render_one(db, conversation, member, message, current_user)
    notify.emit_group(conversation.id, "message_updated", {"message": neutral_dict(message) | {"is_edited": True, "edited_at": now}})
    return rendered


def _refresh_preview(db: Session, conversation: Conversation) -> None:
    latest = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id, Message.is_deleted.is_(False), Message.hidden_kind.is_(None))
        .order_by(Message.created_at.desc(), Message.id.desc()).first()
    )
    conversation.last_message_preview = chat_repo.message_list_preview(latest) if latest else None
    conversation.last_message_at = latest.created_at if latest else conversation.last_message_at


def retract_message(db: Session, current_user: dict, conversation_id: UUID, message_id: UUID) -> dict:
    conversation = common.load_group(db, conversation_id)
    member = common.active_member(db, conversation, current_user)
    message = _message_in_group(db, conversation, member, message_id)
    if message.sender_id != member.user_id:
        raise HTTPException(status_code=403, detail="You can only retract your own messages")
    if message.message_type == "system":
        raise HTTPException(status_code=400, detail="System messages cannot be retracted")
    if message.hidden_kind:
        return render_one(db, conversation, member, message, current_user)
    now = datetime.utcnow()
    message.hidden_kind, message.hidden_by, message.hidden_at, message.pinned_at, message.pinned_by = "retracted", member.user_id, now, None, None
    common.audit(db, conversation, actor=current_user, action="message_retracted", message_id=message.id, target_user_id=message.sender_id)
    _refresh_preview(db, conversation)
    db.commit()
    db.refresh(message)
    notify.emit_group(conversation.id, "message_hidden", {"message_id": str(message.id), "status": "retracted", "placeholder": PLACEHOLDERS["retracted"]})
    return render_one(db, conversation, member, message, current_user)


def hide_message(db: Session, current_user: dict, conversation_id: UUID, message_id: UUID, payload) -> dict:
    """An admin takes any message out of the group (moderation). A "Message removed by admin" note stays in its place."""
    conversation = common.load_group(db, conversation_id)
    member = common.active_member(db, conversation, current_user)
    common.require_admin(member, "remove a message")
    message = _message_in_group(db, conversation, member, message_id)
    if message.message_type == "system":
        raise HTTPException(status_code=400, detail="System messages cannot be removed")
    if message.hidden_kind:
        return render_one(db, conversation, member, message, current_user)
    now = datetime.utcnow()
    message.hidden_kind, message.hidden_by, message.hidden_reason, message.hidden_at = "admin", member.user_id, payload.reason, now
    message.pinned_at = message.pinned_by = None
    common.audit(db, conversation, actor=current_user, action="message_hidden", message_id=message.id, target_user_id=message.sender_id,
                 reason=payload.reason)
    _refresh_preview(db, conversation)
    db.commit()
    db.refresh(message)
    notify.emit_group(conversation.id, "message_hidden", {"message_id": str(message.id), "status": "removed_by_admin", "placeholder": PLACEHOLDERS["admin"]})
    return render_one(db, conversation, member, message, current_user)


# ---------------------------------------------------------------- pins

def pin_message(db: Session, current_user: dict, conversation_id: UUID, message_id: UUID, pinned: bool) -> dict:
    conversation = common.load_group(db, conversation_id)
    member = common.active_member(db, conversation, current_user)
    if member.member_kind != "provider":
        raise HTTPException(status_code=403, detail="Only providers can pin messages")
    message = _message_in_group(db, conversation, member, message_id)
    if message.hidden_kind or message.message_type == "system":
        raise HTTPException(status_code=409, detail="This message cannot be pinned")
    if pinned and message.pinned_at is None:
        message.pinned_at, message.pinned_by = datetime.utcnow(), member.user_id
        common.audit(db, conversation, actor=current_user, action="message_pinned", message_id=message.id)
    elif not pinned and message.pinned_at is not None:
        message.pinned_at = message.pinned_by = None
        common.audit(db, conversation, actor=current_user, action="message_unpinned", message_id=message.id)
    else:
        return render_one(db, conversation, member, message, current_user)
    db.commit()
    db.refresh(message)
    notify.emit_group(conversation.id, "message_pinned", {"message_id": str(message.id), "pinned": pinned})
    return render_one(db, conversation, member, message, current_user)


def list_pinned(db: Session, current_user: dict, conversation_id: UUID) -> dict:
    conversation = common.load_group(db, conversation_id)
    member = common.active_member(db, conversation, current_user)
    query = db.query(Message).filter(Message.conversation_id == conversation.id, Message.pinned_at.isnot(None), Message.hidden_kind.is_(None),
                                     Message.is_deleted.is_(False))
    if member.history_from is not None:
        query = query.filter(Message.created_at > member.history_from)
    items = query.order_by(Message.pinned_at.desc()).all()
    return {"items": render_messages(db, conversation, member, items, current_user), "has_more": False, "next_cursor": None,
            "unread_count": 0, "first_unread_message_id": None, "pinned_count": len(items)}


# ---------------------------------------------------------------- reactions (never in a care group)

def _reaction_summary(db: Session, message: Message, viewer_id: UUID) -> dict:
    counts, mine = _reactions_for(db, [message.id], viewer_id)
    return {"message_id": message.id, "reactions": counts.get(message.id, {}), "my_reactions": mine.get(message.id, [])}


def react(db: Session, current_user: dict, conversation_id: UUID, message_id: UUID, emoji: str, *, add: bool) -> dict:
    conversation = common.load_group(db, conversation_id)
    member = common.active_member(db, conversation, current_user)
    if conversation.group_kind == "care":
        raise HTTPException(status_code=403, detail="Reactions are turned off in care groups")
    message = _message_in_group(db, conversation, member, message_id)
    common.assert_open(conversation)
    if message.hidden_kind:
        raise HTTPException(status_code=409, detail="You cannot react to a removed message")
    if emoji not in settings.group_program_reactions:
        raise HTTPException(status_code=422, detail="That reaction is not available", headers=None)
    existing = (
        db.query(MessageReaction)
        .filter(MessageReaction.message_id == message.id, MessageReaction.user_id == member.user_id, MessageReaction.emoji == emoji).first()
    )
    if add and existing is None:
        db.add(MessageReaction(message_id=message.id, conversation_id=conversation.id, user_id=member.user_id, emoji=emoji))
        db.commit()
    elif not add and existing is not None:
        db.delete(existing)
        db.commit()
    summary = _reaction_summary(db, message, member.user_id)
    notify.emit_group(conversation.id, "reaction_updated", {"message_id": str(message.id), "reactions": summary["reactions"]})
    return summary


def allowed_reactions(db: Session, current_user: dict, conversation_id: UUID) -> dict:
    conversation = common.load_group(db, conversation_id)
    common.active_member(db, conversation, current_user)
    return {"enabled": conversation.group_kind != "care", "emojis": [] if conversation.group_kind == "care" else settings.group_program_reactions}


# ---------------------------------------------------------------- who has read it

def read_status(db: Session, current_user: dict, conversation_id: UUID, message_id: UUID) -> dict:
    conversation = common.load_group(db, conversation_id)
    member = common.active_member(db, conversation, current_user)
    message = _message_in_group(db, conversation, member, message_id)
    if message.message_type == "system":
        raise HTTPException(status_code=400, detail="System messages have no read status")
    # In a program group patients never see who has read a message; providers do.
    if conversation.group_kind == "program" and member.member_kind != "provider":
        raise HTTPException(status_code=403, detail="Read status is not shown to patients in a program group")
    receipts: dict[UUID, set[UUID]] = {}
    for r in db.query(MessageReadReceipt).filter(MessageReadReceipt.message_id == message.id).all():
        receipts.setdefault(message.id, set()).add(r.user_id)
    seen, recipient_count = _seen_by(conversation, message, common.active_members(db, conversation.id), receipts)
    return {"message_id": message.id, "seen_by_count": len(seen), "recipient_count": recipient_count,
            "label": f"seen by {len(seen)} of {recipient_count}", "seen_by": seen}


# ---------------------------------------------------------------- search and files (only what was sent after joining)

def search_messages(db: Session, current_user: dict, conversation_id: UUID, q: str, *, limit: int = 50) -> dict:
    conversation = common.load_group(db, conversation_id)
    member = common.active_member(db, conversation, current_user)
    text = (q or "").strip()
    if len(text) < 2:
        raise HTTPException(status_code=422, detail="Type at least 2 characters to search")
    query = db.query(Message).filter(
        Message.conversation_id == conversation.id, Message.is_deleted.is_(False), Message.hidden_kind.is_(None),
        Message.message_type == "text", Message.content.ilike(f"%{text}%"),
    )
    if member.history_from is not None:
        query = query.filter(Message.created_at > member.history_from)
    items = query.order_by(Message.created_at.desc()).limit(limit).all()
    return {"items": render_messages(db, conversation, member, items, current_user), "has_more": False, "next_cursor": None,
            "unread_count": 0, "first_unread_message_id": None, "pinned_count": 0}


def list_files(db: Session, current_user: dict, conversation_id: UUID, *, limit: int = 100) -> dict:
    conversation = common.load_group(db, conversation_id)
    member = common.active_member(db, conversation, current_user)
    query = (
        db.query(ChatAttachment, Message)
        .join(Message, Message.id == ChatAttachment.message_id)
        .filter(ChatAttachment.conversation_id == conversation.id, ChatAttachment.is_deleted.is_(False),
                Message.is_deleted.is_(False), Message.hidden_kind.is_(None))
    )
    if member.history_from is not None:
        query = query.filter(Message.created_at > member.history_from)
    rows = query.order_by(Message.created_at.desc()).limit(limit).all()
    return {"items": [
        {"attachment_id": a.id, "message_id": m.id, "sender_id": m.sender_id, "file_name": a.file_name, "mime_type": a.mime_type,
         "file_size": a.file_size, "attachment_type": a.attachment_type, "created_at": m.created_at}
        for a, m in rows
    ]}


# ---------------------------------------------------------------- the clinical record and the secure record

def save_to_record(db: Session, current_user: dict, conversation_id: UUID, message_id: UUID, payload, access_token: str | None = None) -> dict:
    """A provider saves a message to a patient's clinical record. A snapshot is kept: the message can later be edited or
    hidden in the group without changing what was saved."""
    conversation = common.load_group(db, conversation_id)
    member = common.active_member(db, conversation, current_user)
    if member.member_kind != "provider":
        raise HTTPException(status_code=403, detail="Only providers can save a message to a clinical record")
    message = _message_in_group(db, conversation, member, message_id)
    if message.hidden_kind or message.message_type == "system":
        raise HTTPException(status_code=409, detail="This message cannot be saved to a record")
    patient = common.get_member(db, conversation.id, payload.patient_user_id)
    if patient is None or patient.membership_status != "active" or patient.member_kind != "patient":
        raise HTTPException(status_code=422, detail="Choose a patient who is an active member of this group")
    if db.query(ClinicalRecordEntry.id).filter(ClinicalRecordEntry.message_id == message.id,
                                               ClinicalRecordEntry.patient_user_id == patient.user_id).first():
        raise HTTPException(status_code=409, detail="This message is already saved to that patient's record")
    entry = ClinicalRecordEntry(
        conversation_id=conversation.id, tenant_id=conversation.tenant_id, message_id=message.id, patient_user_id=patient.user_id,
        saved_by=member.user_id, sender_id=message.sender_id, content_snapshot=message.content, attachment_id=message.attachment_id,
        message_created_at=message.created_at, note=payload.note,
    )
    db.add(entry)
    common.audit(db, conversation, actor=current_user, action="clinical_record_saved", message_id=message.id, target_user_id=patient.user_id)
    db.commit()
    db.refresh(entry)
    return entry


def list_clinical_records(db: Session, current_user: dict, conversation_id: UUID, access_token: str | None = None) -> list[ClinicalRecordEntry]:
    conversation = common.load_group(db, conversation_id)
    member = common.get_member(db, conversation.id, common.user_uuid(current_user))
    is_provider = member is not None and member.membership_status == "active" and member.member_kind == "provider"
    if not is_provider and not common.is_compliance_admin(db, current_user, conversation, access_token):
        raise HTTPException(status_code=403, detail="Only providers of this group and compliance can read its clinical record entries")
    return db.query(ClinicalRecordEntry).filter(ClinicalRecordEntry.conversation_id == conversation.id).order_by(ClinicalRecordEntry.created_at.desc()).all()


def original_message(db: Session, current_user: dict, conversation_id: UUID, message_id: UUID, access_token: str | None = None) -> dict:
    """The secure record: the text as first written, every earlier version, and who hid it and why. Only the owner and
    compliance may read it, and each read is written to the audit trail."""
    conversation = common.load_group(db, conversation_id)
    member = common.get_member(db, conversation.id, common.user_uuid(current_user))
    is_owner = member is not None and member.membership_status == "active" and member.group_role == "owner"
    if not is_owner and not common.is_compliance_admin(db, current_user, conversation, access_token):
        raise HTTPException(status_code=403, detail="Only the group owner and compliance can read the secure record")
    message = chat_repo.get_message_by_id(db, message_id)
    if message is None or message.conversation_id != conversation.id:
        raise HTTPException(status_code=404, detail="Message not found")
    versions = list(message.edit_history or [])
    first = versions[0]["content"] if versions else message.content
    common.audit(db, conversation, actor=current_user, action="original_viewed", message_id=message.id, target_user_id=message.sender_id)
    db.commit()
    return {
        "message_id": message.id, "sender_id": message.sender_id, "status": STATUS_BY_HIDDEN.get(message.hidden_kind, "visible"),
        "content": first, "versions": versions, "current_content": message.content, "hidden_by": message.hidden_by, "hidden_reason": message.hidden_reason,
        "hidden_at": message.hidden_at, "created_at": message.created_at,
    }
