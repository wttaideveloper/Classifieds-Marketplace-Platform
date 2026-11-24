"""Copied from app/services/notification_triggers.py: user id from an e-mail (the legacy chat_users lookup)."""
import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _resolve_user_id_from_email(db: Session, participant_email: str) -> UUID | None:
    """Best-effort lookup: try to find a user ID by email across relevant tables.

    Uses raw SQL to avoid hard dependencies on models that may not exist.
    Returns a UUID if found, None otherwise. Does not raise.
    """
    if not participant_email:
        return None
    try:
        from sqlalchemy import text
        # Wrap the raw SQL in a savepoint (nested transaction) so if it fails 
        # (e.g. table does not exist), it rolls back cleanly without poisoning the session.
        with db.begin_nested():
            # Try chat_users table (legacy/external table)
            result = db.execute(
                text("SELECT id FROM chat_users WHERE email = :email LIMIT 1"),
                {"email": participant_email},
            ).fetchone()
            if result:
                return UUID(str(result[0]))
    except Exception as e:
        logger.debug("User lookup by email %s failed: %s", participant_email, e)
    return None

