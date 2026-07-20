import asyncio
import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.repository import chat_repo
from app.repository import notification_repo
from app.services.firebase_push_service import send_push_to_tokens

logger = logging.getLogger(__name__)


def _emit_realtime_notification(user_id: UUID, payload: dict) -> None:
    try:
        from app.realtime.emitters import emit_notification

        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(emit_notification(str(user_id), payload))
        else:
            loop.run_until_complete(emit_notification(str(user_id), payload))
    except Exception:
        logger.exception("Failed to emit realtime notification for user_id=%s", user_id)


def deliver_notification_to_users(
    db: Session,
    *,
    notification_id: UUID,
    title: str,
    body: str,
    user_ids: list[UUID],
    channels: list[str],
    metadata: dict | None = None,
) -> int:
    """Deliver in-app + optional push channels and write delivery logs."""
    notification_repo.create_user_notifications(db, notification_id, user_ids)
    delivered = 0
    payload_data = {key: str(value) for key, value in (metadata or {}).items()}

    for user_id in user_ids:
        channel_delivered = False

        if "in_app" in channels:
            notification_repo.create_notification_log(
                db,
                notification_id=notification_id,
                recipient_id=user_id,
                channel="in_app",
                status="sent",
            )
            _emit_realtime_notification(
                user_id,
                {
                    "notification_id": str(notification_id),
                    "title": title,
                    "message": body,
                    "metadata": metadata or {},
                    "created_at": datetime.utcnow().isoformat() + "Z",
                },
            )
            channel_delivered = True

        if "push" in channels:
            prefs = chat_repo.get_notification_preferences(db, user_id)
            tokens = [
                device.token
                for device in chat_repo.get_active_device_tokens(db, user_id)
                if device.is_active
            ]
            if prefs.push_enabled and tokens:
                result = send_push_to_tokens(
                    tokens,
                    title=title,
                    body=body,
                    data=payload_data or None,
                )
                status = "sent" if result.sent_count > 0 else "failed"
                error = result.credentials_error or (
                    result.failures[0]["message"] if result.failures else None
                )
                notification_repo.create_notification_log(
                    db,
                    notification_id=notification_id,
                    recipient_id=user_id,
                    channel="push",
                    status=status,
                    error_message=error,
                )
                if result.sent_count > 0:
                    channel_delivered = True
            else:
                notification_repo.create_notification_log(
                    db,
                    notification_id=notification_id,
                    recipient_id=user_id,
                    channel="push",
                    status="skipped",
                    error_message="Push disabled or no registered device tokens.",
                )

        if "email" in channels:
            notification_repo.create_notification_log(
                db,
                notification_id=notification_id,
                recipient_id=user_id,
                channel="email",
                status="queued",
                error_message="Email channel queued for future worker integration.",
            )

        if "sms" in channels:
            notification_repo.create_notification_log(
                db,
                notification_id=notification_id,
                recipient_id=user_id,
                channel="sms",
                status="queued",
                error_message="SMS channel queued for future worker integration.",
            )

        if channel_delivered:
            delivered += 1

    return delivered
