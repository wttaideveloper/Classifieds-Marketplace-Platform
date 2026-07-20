"""Celery tasks for platform notifications."""

from uuid import UUID

from app.celery_app import celery_app


@celery_app.task(name="notifications.deliver_scheduled")
def deliver_scheduled_notification_task(
    notification_id: str,
    user_ids: list[str],
    channels: list[str],
):
    from app.db.database import SessionLocal
    from app.repository import notification_repo
    from app.services.notification_delivery_service import deliver_notification_to_users

    db = SessionLocal()
    try:
        row = notification_repo.get_notification_by_id(db, UUID(notification_id))
        if not row:
            return {"status": "missing"}
        recipients = [UUID(user_id) for user_id in user_ids]
        delivered = deliver_notification_to_users(
            db,
            notification_id=row.id,
            title=row.title,
            body=row.message,
            user_ids=recipients,
            channels=channels,
            metadata=row.metadata_json or {},
        )
        notification_repo.update_notification(
            db,
            row,
            {"status": "sent" if delivered else "failed"},
        )
        return {"status": "sent" if delivered else "failed", "delivered": delivered}
    finally:
        db.close()


@celery_app.task(name="notifications.process_due_scheduled")
def process_due_scheduled_notifications_task():
    from app.db.database import SessionLocal
    from app.repository import notification_repo
    from app.services.notification_delivery_service import deliver_notification_to_users

    db = SessionLocal()
    processed = 0
    try:
        due_rows = notification_repo.list_due_scheduled_notifications(db)
        for row in due_rows:
            metadata = row.metadata_json or {}
            raw_ids = metadata.get("recipient_user_ids") or []
            channels = metadata.get("channels") or ["in_app", "push"]
            recipients = [UUID(str(user_id)) for user_id in raw_ids]
            if not recipients:
                notification_repo.update_notification(db, row, {"status": "failed"})
                continue
            delivered = deliver_notification_to_users(
                db,
                notification_id=row.id,
                title=row.title,
                body=row.message,
                user_ids=recipients,
                channels=channels,
                metadata=metadata,
            )
            notification_repo.update_notification(
                db,
                row,
                {"status": "sent" if delivered else "failed"},
            )
            processed += 1
        return {"processed": processed}
    finally:
        db.close()
