"""The scheduled finance jobs.

    rates         fetch the latest exchange rates (a pinned manual rate is kept)
    payments      ask the gateway about payments whose answer was lost; close the ones nobody paid
    refunds       send refunds stuck at the gateway again; escalate the ones past their target time
    settlements   settle every business whose turn it is
    reminders     one reminder for each payment that failed and was not retried

They run on an in-process thread (like the training reminders: no Celery worker or beat is deployed) every
PAYMENT_JOBS_INTERVAL_SECONDS, and a Super Admin can run them by hand with POST /finance/jobs/run. With several workers, a
database lock lets only one of them run a round; every job is also safe to run twice.
"""
import logging
import threading
import time

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings

logger = logging.getLogger(__name__)

JOBS = ("rates", "payments", "refunds", "settlements", "reminders")
LOCK_KEY = 7_204_001


def run_jobs(db: Session, which: tuple[str, ...] = JOBS, *, access_token: str | None = None) -> dict:
    from app.services import currency_service, payment_service, refund_service, settlement_service

    out: dict = {}
    for job in which:
        try:
            if job == "rates":
                out[job] = currency_service.refresh_rates(db)
            elif job == "payments":
                out[job] = payment_service.reconcile_pending(db, access_token=access_token)
            elif job == "refunds":
                out[job] = {"retried": refund_service.retry_stuck(db), "escalated": refund_service.escalate_overdue(db, access_token=access_token)}
            elif job == "settlements":
                out[job] = settlement_service.run_due(db)
            elif job == "reminders":
                out[job] = {"sent": payment_service.send_retry_reminders(db)}
            else:
                out[job] = {"error": "unknown job"}
        except Exception as exc:
            db.rollback()
            logger.exception("payment job %s failed", job)
            out[job] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    return out


def _try_lock(db: Session) -> bool:
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return True
    return bool(db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar())


def _unlock(db: Session) -> None:
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
        db.commit()


def _loop(interval: int) -> None:
    from app.db.database import SessionLocal

    time.sleep(30)                              # let the app finish starting
    while True:
        try:
            with SessionLocal() as db:
                if _try_lock(db):
                    try:
                        result = run_jobs(db)
                        logger.info("payment jobs: %s", result)
                    finally:
                        _unlock(db)
        except Exception:
            logger.exception("payment job round failed")
        time.sleep(interval)


def start_scheduler() -> threading.Thread | None:
    if not settings.payment_jobs_enabled:
        return None
    thread = threading.Thread(target=_loop, args=(settings.PAYMENT_JOBS_INTERVAL_SECONDS,), name="payment-jobs", daemon=True)
    thread.start()
    logger.info("Payment job scheduler started (every %ss)", settings.PAYMENT_JOBS_INTERVAL_SECONDS)
    return thread
