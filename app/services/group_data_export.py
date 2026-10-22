"""Database-backed group data export queue and private, bounded JSON storage."""
import io
import json
import logging
import threading
import time
from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, or_

from app.models.chat_model import ChatAttachment, Conversation, ConversationParticipant, Message
from app.models.group_chat_model import GroupDataRequest
from app.services.group_chat_common import user_uuid

logger = logging.getLogger(__name__)
MAX_BYTES = 10 * 1024 * 1024
MAX_ATTEMPTS = 3
LEASE = timedelta(minutes=15)
RETENTION = timedelta(days=7)


def _serialize(db, job):
    """Include only the owner's membership and authored visible messages, never attachment paths or bytes."""
    out = io.StringIO()
    size = 0

    def write(value):
        nonlocal size
        size += len(value.encode("utf-8"))
        if size > MAX_BYTES:
            raise ValueError("export_too_large")
        out.write(value)

    def dump(value):
        return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))

    member_filter = [ConversationParticipant.user_id == job.user_id, Conversation.conversation_type == "group"]
    if job.conversation_id:
        member_filter.append(Conversation.id == job.conversation_id)
    write('{"format":"group-chat-data-export","version":1,"memberships":[')
    members = (db.query(ConversationParticipant, Conversation).join(Conversation, Conversation.id == ConversationParticipant.conversation_id)
               .filter(*member_filter).order_by(Conversation.id).yield_per(100))
    first = True
    for member, group in members:
        item = {"conversation_id": group.id, "membership_status": member.membership_status, "joined_at": member.joined_at,
                "left_at": member.left_at, "group_role": member.group_role}
        if member.membership_status == "active" and not group.is_deleted:
            item.update(group_name=group.subject, group_kind=group.group_kind, description=group.description)
        if not first:
            write(",")
        write(dump(item)); first = False
    write('],"messages":[')
    messages = (db.query(Message, ChatAttachment.id).join(Conversation, Conversation.id == Message.conversation_id)
                .join(ConversationParticipant, ConversationParticipant.conversation_id == Conversation.id)
                .outerjoin(ChatAttachment, and_(ChatAttachment.id == Message.attachment_id, ChatAttachment.conversation_id == Message.conversation_id,
                                                ChatAttachment.uploaded_by == job.user_id, ChatAttachment.is_deleted.is_(False)))
                .filter(*member_filter, Message.sender_id == job.user_id, Message.message_type != "system", Message.is_deleted.is_(False), Message.hidden_kind.is_(None))
                .order_by(Message.created_at, Message.id).yield_per(100))
    first = True
    for message, attachment_id in messages:
        if message.content and len(message.content.encode("utf-8")) > MAX_BYTES:
            raise ValueError("export_too_large")
        item = {"id": message.id, "conversation_id": message.conversation_id, "content": message.content,
                "message_type": message.message_type, "attachment_id": attachment_id,
                "created_at": message.created_at, "edited_at": message.edited_at}
        if not first:
            write(",")
        write(dump(item)); first = False
    write("]}")
    return out.getvalue()


def process_one(db, now=None):
    now = now or datetime.utcnow()
    db.query(GroupDataRequest).filter(GroupDataRequest.status == "completed", GroupDataRequest.expires_at <= now,
                                      GroupDataRequest.export_json.isnot(None)).update({"export_json": None}, synchronize_session=False)
    # A pre-migration in_progress row has no lease and may have been handled
    # manually. Leave it untouched instead of assuming it is an abandoned job.
    stale = ((GroupDataRequest.status == "in_progress") & GroupDataRequest.processing_started_at.isnot(None)
             & (GroupDataRequest.processing_started_at < now - LEASE))
    db.query(GroupDataRequest).filter(stale, GroupDataRequest.attempts >= MAX_ATTEMPTS).update(
        {"status": "failed", "error_code": "processing_interrupted", "processing_token": None}, synchronize_session=False)
    candidate = (db.query(GroupDataRequest.id).filter(GroupDataRequest.request_type == "copy", GroupDataRequest.attempts < MAX_ATTEMPTS,
                  or_(GroupDataRequest.status == "received", stale)).order_by(GroupDataRequest.created_at).first())
    if candidate is None:
        db.commit()
        return False
    job_id, token = candidate[0], uuid4()
    claimed = db.query(GroupDataRequest).filter(GroupDataRequest.id == job_id, GroupDataRequest.attempts < MAX_ATTEMPTS,
                  or_(GroupDataRequest.status == "received", stale)).update({"status": "in_progress", "processing_started_at": now,
                  "processing_token": token, "attempts": GroupDataRequest.attempts + 1, "error_code": None}, synchronize_session=False)
    db.commit()
    if not claimed:
        return False
    logger.info("Group export %s claimed", job_id)
    try:
        job = db.get(GroupDataRequest, job_id)
        data = _serialize(db, job)
        done = datetime.utcnow()
        db.query(GroupDataRequest).filter_by(id=job_id, status="in_progress", processing_token=token).update(
            {"status": "completed", "export_json": data, "completed_at": done, "expires_at": done + RETENTION,
             "processing_token": None, "error_code": None}, synchronize_session=False)
        db.commit()
        logger.info("Group export %s processing finished", job_id)
    except Exception as exc:
        db.rollback()
        logger.error("Group export %s failed (%s)", job_id, type(exc).__name__)
        code = "export_too_large" if isinstance(exc, ValueError) and str(exc) == "export_too_large" else "generation_failed"
        db.query(GroupDataRequest).filter_by(id=job_id, status="in_progress", processing_token=token).update(
            {"status": "failed", "error_code": code, "processing_token": None}, synchronize_session=False)
        db.commit()
    return True


def owned(db, request_id, user):
    job = db.query(GroupDataRequest).filter_by(id=request_id, user_id=user_uuid(user)).first()
    if job is None:
        raise HTTPException(404, "Data request not found")
    return job


def retry(db, request_id, user):
    job = owned(db, request_id, user)
    changed = db.query(GroupDataRequest).filter_by(id=job.id, user_id=job.user_id, status="failed").update(
        {"status": "received", "attempts": 0, "error_code": None, "processing_started_at": None}, synchronize_session=False)
    if not changed:
        raise HTTPException(409, "Only failed requests can be retried")
    db.commit()
    db.expire_all()
    return owned(db, request_id, user)


def download(db, request_id, user):
    job = owned(db, request_id, user)
    if job.status != "completed":
        raise HTTPException(409, "Export is not completed")
    if not job.export_json or not job.expires_at or job.expires_at <= datetime.utcnow():
        raise HTTPException(410, "Export is no longer available; submit a new request")
    return job.export_json


def _loop():
    from app.db.database import SessionLocal
    while True:
        try:
            with SessionLocal() as db:
                process_one(db)
        except Exception as exc:
            logger.error("Group export worker failed (%s)", type(exc).__name__)
        time.sleep(30)


def start_scheduler():
    thread = threading.Thread(target=_loop, name="group-data-export", daemon=True)
    thread.start()
    return thread
