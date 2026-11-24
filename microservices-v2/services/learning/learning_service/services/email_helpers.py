"""E-mail helpers learning shares with events (copied from app/services/notification_triggers.py)."""
import logging
import smtplib
from email.message import EmailMessage
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from mskit.settings import settings

logger = logging.getLogger(__name__)


def looks_like_email(value) -> bool:
    return bool(value) and bool(_EMAIL_SHAPE.match(str(value).strip()))


def _send_email_via_smtp(recipient_email: str, title: str, body: str) -> bool:
    """Send a plain-text email using the configured SMTP settings.

    Returns True if the message was accepted by the SMTP server, False otherwise. When it is False the reason is
    in `_last_email_error`.
    """
    _last_email_error.set(None)
    if not smtp_configured():
        _last_email_error.set("SMTP is not configured on the server (email_user / email_pass are empty).")
        logger.warning("SMTP not configured (email_user/email_pass missing) — skipping email to %s", recipient_email)
        return False
    try:
        msg = _build_message(recipient_email, title, body)
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
            smtp.starttls()
            smtp.login(settings.email_user, settings.email_pass)
            smtp.send_message(msg)
        logger.info("Email sent to %s: %s", recipient_email, title)
        return True
    except Exception as e:
        _last_email_error.set(_explain_smtp_error(e))
        logger.warning("SMTP email send failed to %s: %s", recipient_email, e)
        return False


def check_smtp(send_to: str | None = None) -> dict:
    """Try the configured mail server step by step and say which step fails. No password is ever returned."""
    report: dict = {
        "configured": smtp_configured(), "host": settings.SMTP_HOST, "port": settings.SMTP_PORT,
        "from": settings.email_user or None, "steps": {},
    }
    if not report["configured"]:
        report["ok"] = False
        report["problem"] = "email_user / email_pass are empty on this server."
        return report
    smtp = None
    try:
        try:
            smtp = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS)
            report["steps"]["connect"] = "ok"
        except Exception as e:
            report["steps"]["connect"] = "failed"
            report["ok"], report["problem"] = False, _explain_smtp_error(e)
            return report
        try:
            smtp.starttls()
            report["steps"]["starttls"] = "ok"
        except Exception as e:
            report["steps"]["starttls"] = "failed"
            report["ok"], report["problem"] = False, f"STARTTLS failed: {e.__class__.__name__}: {e}"
            return report
        try:
            smtp.login(settings.email_user, settings.email_pass)
            report["steps"]["login"] = "ok"
        except Exception as e:
            report["steps"]["login"] = "failed"
            report["ok"], report["problem"] = False, _explain_smtp_error(e)
            return report
        if send_to:
            try:
                smtp.send_message(_build_message(send_to, "Test email from the platform", "If you can read this, outgoing email works."))
                report["steps"]["send_test"] = "ok"
            except Exception as e:
                report["steps"]["send_test"] = "failed"
                report["ok"], report["problem"] = False, _explain_smtp_error(e)
                return report
        report["ok"] = True
        return report
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                pass


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

