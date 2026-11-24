"""Internal API of the notifications service (/internal/v1, shared-key protected, not routed by the gateway).

Replaces the in-process calls other domains made into notification_service / notification_idempotency.
"""
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mskit.app import require_internal_key
from notifications_service.db.database import get_db
from notifications_service.models.notification_model import Notification, UserNotification  # recorded_notifications
from notifications_service.services import notification_idempotency
from notifications_service.services.notification_service import create_automatic_notification

router = APIRouter(dependencies=[Depends(require_internal_key)], tags=["internal"])


class AutomaticNotification(BaseModel):
    title: str
    message: str
    category: str
    user_ids: list[UUID]
    tenant_id: UUID | None = None
    metadata: dict | None = None
    channels: list[str] | None = None


class DedupeKey(BaseModel):
    key: str


@router.post("/notifications/automatic", status_code=201)
def automatic(payload: AutomaticNotification, db: Session = Depends(get_db)):
    row = create_automatic_notification(
        db, title=payload.title, message=payload.message, category=payload.category, user_ids=payload.user_ids,
        tenant_id=payload.tenant_id, metadata=payload.metadata, channels=payload.channels,
    )
    if row is None:
        return {"id": None, "status": None}
    return {"id": str(row.id), "status": row.status}


@router.post("/notification-dedupe/claim")
def claim(payload: DedupeKey, db: Session = Depends(get_db)):
    return {"claimed": notification_idempotency.claim(db, payload.key)}


@router.post("/notification-dedupe/release")
def release(payload: DedupeKey, db: Session = Depends(get_db)):
    notification_idempotency.release(db, payload.key)
    return {"released": True}


class NotificationRecord(BaseModel):
    tenant_id: UUID | None = None
    created_by: UUID | None = None
    title: str
    message: str
    notification_type: str = "automatic"
    category: str
    delivery_type: str = "immediate"
    status: str
    metadata: dict | None = None


@router.post("/notifications/records", status_code=201)
def record(payload: NotificationRecord, db: Session = Depends(get_db)):
    """A notification row written for audit/trace (no delivery), as notification_triggers did."""
    from notifications_service.repository import notification_repo

    row = notification_repo.create_notification(
        db, tenant_id=payload.tenant_id, created_by=payload.created_by, title=payload.title, message=payload.message,
        notification_type=payload.notification_type, category=payload.category, delivery_type=payload.delivery_type,
        status=payload.status, metadata=payload.metadata or {},
    )
    return {"id": str(row.id)}


class MarkReadByReference(BaseModel):
    user_id: UUID
    category_like: str
    reference: str


@router.post("/notifications/mark-read-by-reference")
def mark_read_by_reference(payload: MarkReadByReference, db: Session = Depends(get_db)):
    """Unread notifications of the user, in categories LIKE category_like, whose metadata mentions reference."""
    from datetime import datetime

    from sqlalchemy import String, cast

    from notifications_service.models.notification_model import Notification, UserNotification

    ids = [r[0] for r in db.query(Notification.id).filter(Notification.category.like(payload.category_like),
                                                        cast(Notification.metadata_json, String).like(f"%{payload.reference}%")).all()]
    if not ids:
        return {"marked": 0}
    count = db.query(UserNotification).filter(UserNotification.user_id == payload.user_id, UserNotification.is_read.is_(False),
                                              UserNotification.notification_id.in_(ids)).update(
        {UserNotification.is_read: True, UserNotification.read_at: datetime.utcnow()}, synchronize_session=False)
    db.commit()
    return {"marked": count}



# notification rows recorded for an event / training (admin diagnostics in moderation); same query as before
def recorded_notifications(db: Session, entity_id: UUID) -> list[dict]:
    """Notification rows written for this entity and who they were addressed to."""
    rows = (
        db.query(Notification)
        .filter(Notification.metadata_json["entity_id"].astext == str(entity_id))
        .order_by(Notification.created_at.desc())
        .limit(50)
        .all()
    )
    out = []
    for n in rows:
        recipients = [
            str(u.user_id)
            for u in db.query(UserNotification).filter(UserNotification.notification_id == n.id).all()
        ]
        out.append({
            "notification_id": str(n.id),
            "category": n.category,
            "status": n.status,
            "created_at": n.created_at.isoformat() if n.created_at else None,
            "recipient_user_ids": recipients,
        })
    return out



@router.get("/notifications/recorded/{entity_id}")
def recorded(entity_id: UUID, db: Session = Depends(get_db)):
    from notifications_service.models.notification_model import Notification, UserNotification  # noqa: F401 - used by the copy

    return recorded_notifications(db, entity_id)



class _ByConversation(BaseModel):
    user_id: UUID
    conversation_id: UUID


@router.post("/notifications/mark-read-by-conversation")
def mark_read_by_conversation(body: _ByConversation, db: Session = Depends(get_db)):
    """notification_repo.mark_user_notifications_read_by_conversation (chat read state syncs the platform inbox)."""
    from notifications_service.repository import notification_repo

    return {"marked": notification_repo.mark_user_notifications_read_by_conversation(db, body.user_id, body.conversation_id)}
