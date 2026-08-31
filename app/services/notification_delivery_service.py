import asyncio
import logging
import smtplib
from datetime import datetime
from uuid import UUID
from email.message import EmailMessage

from sqlalchemy.orm import Session

from app.core.config import settings
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
            try:
                # Extract email from metadata (participant_email stored by notification_triggers)
                recipient_email = (metadata or {}).get("participant_email", "")
                if not recipient_email:
                    # Try to look up email from chat_users table
                    try:
                        from sqlalchemy import text
                        row = db.execute(text("SELECT email FROM chat_users WHERE id = :uid LIMIT 1"), {"uid": str(user_id)}).fetchone()
                        if row:
                            recipient_email = row[0]
                    except Exception:
                        pass
                if recipient_email:
                    sent = _send_email_notification(recipient_email, title, body)
                else:
                    logger.debug("No email address for user_id=%s, skipping email channel", user_id)
                    sent = False
                if sent:
                    notification_repo.create_notification_log(
                        db,
                        notification_id=notification_id,
                        recipient_id=user_id,
                        channel="email",
                        status="sent",
                    )
                else:
                    notification_repo.create_notification_log(
                        db,
                        notification_id=notification_id,
                        recipient_id=user_id,
                        channel="email",
                        status="failed",
                        error_message="Failed to send email via SMTP.",
                    )
            except Exception as e:
                logger.warning(f"Email send failed for user_id={user_id}: {e}")
                notification_repo.create_notification_log(
                    db,
                    notification_id=notification_id,
                    recipient_id=user_id,
                    channel="email",
                    status="failed",
                    error_message=str(e),
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


def _send_email_notification(recipient_email: str, title: str, body: str) -> bool:
    """Send a plain‑text email using the configured SMTP settings.

    Returns True if the message was accepted by the SMTP server, False otherwise.
    """
    try:
        msg = EmailMessage()
        msg["From"] = settings.email_user
        msg["To"] = recipient_email
        msg["Subject"] = title
        msg.set_content(body)

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as smtp:
            smtp.starttls()
            smtp.login(settings.email_user, settings.email_pass)
            smtp.send_message(msg)
        return True
    except Exception as e:
        logger.exception(f"SMTP email send failed to {recipient_email}")
        return False
