"""At-most-once guard for system-generated notifications.

``claim`` inserts a unique row *before* delivery; whoever inserts first delivers, every retry,
duplicate webhook or concurrent worker gets ``False`` and stays silent. ``release`` deletes the
row when delivery produced nothing, so a later retry can still deliver.
"""
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.notification_model import NotificationEventLog


def claim(db: Session, dedupe_key: str) -> bool:
    db.add(NotificationEventLog(dedupe_key=dedupe_key[:255]))
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def release(db: Session, dedupe_key: str) -> None:
    db.query(NotificationEventLog).filter(NotificationEventLog.dedupe_key == dedupe_key[:255]).delete()
    db.commit()
