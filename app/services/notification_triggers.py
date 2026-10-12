import contextvars
import logging
import re
import smtplib
from uuid import UUID, uuid4
from email.message import EmailMessage
from sqlalchemy.orm import Session

from app.core.config import settings

logger = logging.getLogger(__name__)

SMTP_TIMEOUT_SECONDS = 20

# Why the last email sent from this thread/request did not go out (None when it did, or when none was tried).
# _send_email_via_smtp keeps returning a plain bool; callers that want the reason read it here.
_last_email_error: contextvars.ContextVar[str | None] = contextvars.ContextVar("last_email_error", default=None)

_EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def looks_like_email(value) -> bool:
    return bool(value) and bool(_EMAIL_SHAPE.match(str(value).strip()))


def smtp_configured() -> bool:
    return bool(settings.email_user and settings.email_pass)


def _explain_smtp_error(exc: Exception) -> str:
    """A reason an organiser or developer can act on, without credentials or stack traces."""
    host, port = settings.SMTP_HOST, settings.SMTP_PORT
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return ("The mail server rejected the sign-in. Check email_user / email_pass on the server "
                "(Gmail needs an app password, not the account password).")
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "The mail server refused this recipient address."
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return "The mail server refused the sender address (email_user)."
    if isinstance(exc, (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, TimeoutError, ConnectionError, OSError)):
        return (f"Could not reach the mail server {host}:{port} ({exc.__class__.__name__}). "
                "The server may be blocking outbound SMTP, or SMTP_HOST / SMTP_PORT are wrong.")
    return f"{exc.__class__.__name__}: {exc}"


def _build_message(recipient_email: str, title: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = settings.email_user
    msg["To"] = recipient_email
    msg["Subject"] = title
    msg.set_content(body)
    return msg


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


def send_emails_via_smtp(messages: list[tuple[str, str, str]]) -> list[tuple[bool, str | None]]:
    """Send many plain-text emails over ONE SMTP connection and login: [(recipient, subject, body), ...] ->
    [(sent, reason_if_not), ...] in the same order. Signing in once per message is slow and makes providers such
    as Gmail refuse further logins part-way through a batch. One bad recipient never stops the others; a dropped
    connection is reopened once."""
    if not messages:
        return []
    if not smtp_configured():
        reason = "SMTP is not configured on the server (email_user / email_pass are empty)."
        logger.warning("SMTP not configured (email_user/email_pass missing) — skipping %d emails", len(messages))
        return [(False, reason)] * len(messages)

    results: list[tuple[bool, str | None]] = []
    smtp = None

    def connect():
        client = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS)
        client.starttls()
        client.login(settings.email_user, settings.email_pass)
        return client

    try:
        for recipient, title, body in messages:
            for attempt in (1, 2):
                try:
                    if smtp is None:
                        smtp = connect()
                    smtp.send_message(_build_message(recipient, title, body))
                    results.append((True, None))
                    break
                except smtplib.SMTPServerDisconnected:
                    smtp = None  # reopen once, then give up on this message
                    if attempt == 2:
                        results.append((False, _explain_smtp_error(smtplib.SMTPServerDisconnected("connection lost"))))
                except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError) as e:
                    results.append((False, _explain_smtp_error(e)))
                    break
                except Exception as e:  # sign-in rejected, cannot connect, ...: the same will happen to the rest
                    reason = _explain_smtp_error(e)
                    logger.warning("SMTP batch send failed: %s", e)
                    results.append((False, reason))
                    smtp = None
                    break
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                pass
    return results


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


def _emit_inapp_notification(db: Session, user_id: UUID, title: str, message: str, metadata: dict):
    """Emit an in-app notification via the realtime emitter if available."""
    try:
        from app.realtime.emitters import emit_notification
        from app.realtime.loop_bridge import run_coroutine
        notif_id = str(uuid4())
        payload = {"notification_id": notif_id, "title": title, "message": message, "metadata": metadata}
        run_coroutine(emit_notification(str(user_id), payload))
    except Exception as e:
        logger.debug("In-app emit failed for user_id=%s: %s", user_id, e)


def _safe_notify(
    db: Session,
    title: str,
    message: str,
    category: str,
    tenant_id,
    metadata: dict,
    participant_email: str | None = None,
    channels: list[str] | None = None,
    email_result: tuple[bool, str | None] | None = None,
):
    """Best-effort notification using an isolated database session for side effects.

    `email_result` is (sent, reason) for an email the caller already sent as part of a batch; the email is then
    not sent again here, only recorded."""
    channels = channels or ["in_app"]
    email_sent = False
    email_error: str | None = None
    user_ids: list[UUID] = []

    # 1) Send email directly if participant_email is available
    if participant_email and "email" in channels:
        if email_result is not None:
            email_sent, email_error = email_result
        else:
            _last_email_error.set(None)
            email_sent = _send_email_via_smtp(participant_email, title, message)
            email_error = None if email_sent else (_last_email_error.get() or "The email could not be sent.")

    # Use a completely separate session for all notification side-effects
    # to guarantee we never poison the checkout/caller's transaction.
    from app.db.database import SessionLocal
    with SessionLocal() as notif_db:
        # 2) Try to resolve a user_id for in-app/push pipeline
        if participant_email:
            resolved_id = _resolve_user_id_from_email(notif_db, participant_email)
            if resolved_id:
                user_ids.append(resolved_id)
                _emit_inapp_notification(notif_db, resolved_id, title, message, metadata)

        # 3) If we have user_ids, dispatch via the normal automatic notification pipeline (in-app/push)
        if user_ids:
            try:
                from app.services.notification_service import create_automatic_notification
                create_automatic_notification(
                    notif_db, title=title, message=message, category=category,
                    user_ids=user_ids, tenant_id=tenant_id, metadata=metadata, channels=channels,
                )
            except Exception as e:
                logger.debug("automatic notification dispatch failed: %s", e)

        # 4) Always record an audit row so we have a trace
        try:
            from app.repository import notification_repo
            tid = None
            try:
                tid = UUID(str(tenant_id)) if tenant_id else None
            except Exception:
                tid = None
            status = "sent" if email_sent else "audit"
            notification_repo.create_notification(
                notif_db, tenant_id=tid, created_by=None, title=title, message=message,
                notification_type="automatic", category=category, delivery_type="immediate",
                status=status,
                metadata={
                    **metadata,
                    "participant_email": participant_email,
                    "email_sent": email_sent,
                    "user_ids_resolved": [str(uid) for uid in user_ids],
                } if participant_email else metadata,
            )
        except Exception as e:
            logger.debug("audit row create failed: %s", e)

    logger.info(
        "notification trigger: %s title=%s email=%s email_sent=%s user_ids=%s",
        category, title, participant_email, email_sent, user_ids,
    )
    return {"email_attempted": bool(participant_email and "email" in channels), "email_sent": email_sent, "email_error": email_error}


def notify_registration_confirmation(db: Session, event, reg):
    title = f"Registration Confirmed: {event.title}"
    msg = f"Hi {reg.participant_name}, your registration for {event.title} is confirmed. QR: {reg.qr_code}"
    _safe_notify(
        db, title, msg, "booking_confirmed", event.tenant_id,
        {"event_id": str(event.id), "registration_id": str(reg.id), "qr_code": reg.qr_code},
        participant_email=reg.participant_email, channels=["in_app", "push", "email", "sms"],
    )


def notify_single_cancellation(db: Session, event, reg):
    title = f"Registration Cancelled: {event.title}"
    msg = f"Hi {reg.participant_name}, your registration for {event.title} has been cancelled."
    _safe_notify(
        db, title, msg, "event_cancelled", event.tenant_id,
        {"event_id": str(event.id), "registration_id": str(reg.id)},
        participant_email=reg.participant_email, channels=["in_app", "push", "email", "sms"],
    )


def notify_event_cancelled(db: Session, event, previous_status: str):
    title = f"Event Cancelled: {event.title}"
    msg = f"Event {event.title} has been cancelled (was {previous_status})."
    _safe_notify(
        db, title, msg, "event_cancelled", event.tenant_id,
        {"event_id": str(event.id), "previous_status": previous_status},
        channels=["in_app", "push", "email", "sms"],
    )
    try:
        from app.models.event_aux_models import EventRegistration
        regs = db.query(EventRegistration).filter(
            EventRegistration.event_id == event.id,
            EventRegistration.status.in_(["confirmed", "attended"]),
        ).all()
        for r in regs:
            _safe_notify(
                db, title, f"Hi {r.participant_name}, {msg}", "event_cancelled", event.tenant_id,
                {"event_id": str(event.id), "registration_id": str(r.id)},
                participant_email=r.participant_email, channels=["in_app", "push", "email", "sms"],
            )
    except Exception as e:
        logger.warning("fan-out cancelled failed: %s", e)



def notify_payment_success(db: Session, event, order):
    title = f"Payment Confirmed: {event.title}"
    msg = f"Hi {order.participant_name}, payment of {order.amount} {order.currency} for {event.title} confirmed. Order {order.id}"
    _safe_notify(
        db, title, msg, "payment_success", event.tenant_id,
        {"event_id": str(event.id), "order_id": str(order.id), "amount": str(order.amount)},
        participant_email=order.participant_email, channels=["in_app", "push", "email", "sms"],
    )


def notify_payment_failed(db: Session, event, order, reason: str | None = None):
    title = f"Payment Failed: {event.title}"
    msg = f"Hi {order.participant_name}, payment for {event.title} failed" + (f": {reason}" if reason else ".")
    _safe_notify(
        db, title, msg, "payment_failed", event.tenant_id,
        {"event_id": str(event.id), "order_id": str(order.id)},
        participant_email=order.participant_email, channels=["in_app", "push", "email", "sms"],
    )


def _venue_text(venue) -> str | None:
    if isinstance(venue, dict):
        parts = [venue.get(k) for k in ("name", "address", "city") if venue.get(k)]
        return ", ".join(str(p) for p in parts) or None
    return str(venue) if venue else None


def notify_event_reminder(db: Session, event) -> dict:
    """Remind everyone registered for the event (confirmed or attended: online and walk-in registrations alike).

    Returns what actually happened, so the caller can tell the organiser the truth:
      {"total": n, "emailed": n, "failed": n, "skipped": n, "by_source": {"online": {...}, "walk_in": {...}},
       "failures": [{"email", "source", "reason"}], "smtp_configured": bool}
    "emailed" counts emails the mail server accepted; it cannot know whether the person later saw it (spam folder).
    """
    title = f"Reminder: {event.title} tomorrow"
    when = f"{event.start_date} ({event.time_zone})" if getattr(event, "time_zone", None) else f"{event.start_date}"
    lines = [f"Reminder: {event.title} starts {when}."]
    venue = _venue_text(getattr(event, "venue", None))
    if venue:
        lines.append(f"Venue: {venue}")
    if getattr(event, "meeting_link", None):
        lines.append(f"Join online: {event.meeting_link}")
    msg = " ".join(lines)

    summary: dict = {
        "total": 0, "emailed": 0, "failed": 0, "skipped": 0,
        "by_source": {"online": {"total": 0, "emailed": 0, "failed": 0, "skipped": 0},
                      "walk_in": {"total": 0, "emailed": 0, "failed": 0, "skipped": 0}},
        "failures": [], "smtp_configured": smtp_configured(),
    }
    try:
        from app.models.event_aux_models import EventRegistration
        regs = db.query(EventRegistration).filter(
            EventRegistration.event_id == event.id,
            EventRegistration.status.in_(["confirmed", "attended"]),
        ).all()
        sendable = [r for r in regs if looks_like_email((r.participant_email or "").strip())]
        # One SMTP connection and login for everyone, not one per person.
        sent = send_emails_via_smtp([
            ((r.participant_email or "").strip(), title, f"Hi {r.participant_name}, {msg}") for r in sendable
        ])
        email_results = {r.id: result for r, result in zip(sendable, sent)}
        for r in regs:
            source = "walk_in" if getattr(r, "registration_source", None) == "walk_in" else "online"
            bucket = summary["by_source"][source]
            summary["total"] += 1
            bucket["total"] += 1
            email = (r.participant_email or "").strip()
            if r.id not in email_results:
                summary["skipped"] += 1
                bucket["skipped"] += 1
                summary["failures"].append({"email": email or None, "source": source, "reason": "The registration has no valid email address."})
                continue
            outcome = _safe_notify(
                db, title, f"Hi {r.participant_name}, {msg}", "event_reminder", event.tenant_id,
                {"event_id": str(event.id), "registration_id": str(r.id)},
                participant_email=email, channels=["in_app", "push", "email", "sms"],
                email_result=email_results[r.id],
            ) or {}
            if outcome.get("email_sent"):
                summary["emailed"] += 1
                bucket["emailed"] += 1
            else:
                summary["failed"] += 1
                bucket["failed"] += 1
                summary["failures"].append({"email": email, "source": source, "reason": outcome.get("email_error") or "The email could not be sent."})
    except Exception as e:
        logger.warning("reminder failed: %s", e)
        summary["error"] = "The reminder stopped part-way; see the server log."
    logger.info(
        "event reminder %s: total=%s emailed=%s failed=%s skipped=%s", getattr(event, "id", None),
        summary["total"], summary["emailed"], summary["failed"], summary["skipped"],
    )
    return summary


def notify_schedule_change(db: Session, event, changes: dict):
    title = f"Update: {event.title} schedule changed"
    msg = f"Event {event.title} updated: " + ", ".join([f"{k}: {v}" for k, v in changes.items()])
    try:
        from app.models.event_aux_models import EventRegistration
        regs = db.query(EventRegistration).filter(
            EventRegistration.event_id == event.id,
            EventRegistration.status.in_(["confirmed", "attended"]),
        ).all()
        for r in regs:
            _safe_notify(
                db, title, f"Hi {r.participant_name}, {msg}", "event_schedule_change", event.tenant_id,
                {"event_id": str(event.id), "changes": changes, "registration_id": str(r.id)},
                participant_email=r.participant_email, channels=["in_app", "push", "email", "sms"],
            )
    except Exception as e:
        logger.warning("schedule change notify failed: %s", e)


def notify_refund_status(db: Session, event, order_or_reg, status: str):
    title = f"Refund {status}: {event.title}"
    email = getattr(order_or_reg, "participant_email", None)
    msg = f"Refund status for {event.title}: {status}"
    _safe_notify(
        db, title, msg, "refund_status", event.tenant_id,
        {"event_id": str(event.id), "status": status, "id": str(getattr(order_or_reg, "id", ""))},
        participant_email=email, channels=["in_app", "push", "email", "sms"],
    )
