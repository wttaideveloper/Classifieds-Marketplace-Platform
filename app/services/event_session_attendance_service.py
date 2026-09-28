"""Session-level attendance for an Event (Phase 2.4): check-in, undo, check-out, batch, and per-session counts.

Event sessions live inside ``Event.sessions`` (JSONB). Attendance is one ``EventSessionAttendance`` row per
(event, registration, session); it is ADDITIVE and never touches event-level check-in, the registration
status, capacity, payment, orders or the waitlist.

Rules (all operations):
  * the Event must exist and the caller must own it (enforced by the route dependency before this runs);
  * the session id must be one of THIS event's own sessions and non-empty — a session id from another event,
    or a legacy id-less session, is a 404. Nothing here ever writes to ``Event.sessions``: legacy id-less
    sessions get their ids from the existing add/update/delete-session or event-update paths, never from a read;
  * the registration must belong to THIS event (looked up by id or by its existing QR value);
  * eligibility mirrors event-level check-in: event not cancelled/completed/archived/suspended, registration
    not cancelled, not refunded.

Every write stages its audit row in the same transaction as the attendance change and commits once.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Iterable
from uuid import UUID

import sqlalchemy as sa
from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.event_aux_models import EventOrder, EventRegistration, EventSessionAttendance
from app.schemas.event_session_attendance_schema import (
    EventSessionAttendanceResponse,
    EventSessionAttendanceState,
    EventSessionAttendanceSummary,
    EventSessionBatchCheckInResponse,
    EventSessionBatchCheckInResultItem,
)
from app.services.event_service import (
    _actor_id,
    _actor_uuid,
    _find_registration,
    _get_event_or_404,
    _log_audit,
    _registration_is_refunded,
)

# The event states in which event-level check-in is refused (check_in_service / batch_checkin_service).
CHECK_IN_BLOCKED_EVENT_STATUSES = ("cancelled", "completed", "archived", "suspended")
_ACTIVE_STATUSES = ("confirmed", "attended")
# _linked_order_for_registration accepts an order placed up to this long AFTER the registration.
_REFUND_PAIRING_GRACE = timedelta(minutes=5)


# ---------------------------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------------------------


def event_sessions_with_ids(event) -> list[dict]:
    """The event's sessions that can take attendance: dicts with a non-empty id (first wins on a duplicate id).
    Legacy id-less sessions are skipped, never given an id here."""
    seen: set[str] = set()
    sessions: list[dict] = []
    for session in event.sessions or []:
        if not isinstance(session, dict) or session.get("id") in (None, ""):
            continue
        session_id = str(session["id"])
        if session_id in seen:
            continue
        seen.add(session_id)
        sessions.append(session)
    return sessions


def find_session(event, session_id: str) -> dict:
    wanted = str(session_id)
    for session in event_sessions_with_ids(event):
        if str(session["id"]) == wanted:
            return session
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")


# ---------------------------------------------------------------------------------------------
# shared checks + responses
# ---------------------------------------------------------------------------------------------


def _assert_event_accepts_check_in(event) -> None:
    if event.status in CHECK_IN_BLOCKED_EVENT_STATUSES:
        raise HTTPException(status_code=400, detail=f"Cannot check-in: event is {event.status}")


def _assert_registration_can_check_in(db: Session, reg) -> None:
    if reg.status == "cancelled":
        raise HTTPException(status_code=400, detail="Cannot check-in: registration is cancelled")
    if _registration_is_refunded(db, reg):
        raise HTTPException(status_code=400, detail="Cannot check-in: registration was refunded")


def _resolve(db: Session, event_id: UUID, session_id: str, registration_id, qr_code):
    """Event -> its session -> its registration, each scoped to this event (404 otherwise)."""
    event = _get_event_or_404(db, event_id)
    session = find_session(event, session_id)
    reg = _find_registration(db, event_id, registration_id, qr_code)
    if reg is None:
        raise HTTPException(status_code=404, detail="Registration not found")
    return event, session, reg


def _row(db: Session, event_id: UUID, registration_id: UUID, session_id: str):
    return db.execute(
        sa.select(EventSessionAttendance).where(
            EventSessionAttendance.event_id == event_id,
            EventSessionAttendance.registration_id == registration_id,
            EventSessionAttendance.session_id == session_id,
        )
    ).scalar_one_or_none()


def _response(event, session: dict, reg, row, *, message: str, outcome: str) -> EventSessionAttendanceResponse:
    return EventSessionAttendanceResponse(
        message=message,
        outcome=outcome,
        event_id=event.id,
        session_id=str(session["id"]),
        session_title=session.get("title"),
        registration_id=reg.id,
        participant_name=reg.participant_name,
        participant_email=reg.participant_email,
        registration_status=reg.status,
        checked_in=row is not None,
        checked_in_at=row.checked_in_at if row is not None else None,
        checked_in_by=row.checked_in_by if row is not None else None,
        checked_out_at=row.checked_out_at if row is not None else None,
        checked_out_by=row.checked_out_by if row is not None else None,
    )


def _audit_state(reg, session: dict, row=None, **extra) -> dict:
    state = {
        "registration_id": str(reg.id),
        "participant_email": reg.participant_email,
        "session_id": str(session["id"]),
        "session_title": session.get("title"),
        "checked_in": row is not None,
    }
    if row is not None:
        state.update(
            checked_in_at=row.checked_in_at, checked_in_by=row.checked_in_by,
            checked_out_at=row.checked_out_at, checked_out_by=row.checked_out_by,
        )
    state.update(extra)
    return state


def _commit(db: Session) -> None:
    """Commit the attendance change together with its staged audit row, or roll both back."""
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


# ---------------------------------------------------------------------------------------------
# check-in / undo / check-out (single attendee)
# ---------------------------------------------------------------------------------------------


def check_in_session_service(
    db: Session, event_id: UUID, session_id: str, payload, current_user: dict | None = None, *, commit: bool = True
) -> EventSessionAttendanceResponse:
    """``commit=False`` stages the attendance and its audit row in the caller's transaction (walk-in); default unchanged."""
    event, session, reg = _resolve(db, event_id, session_id, payload.registration_id, payload.qr_code)
    _assert_event_accepts_check_in(event)
    _assert_registration_can_check_in(db, reg)
    sid = str(session["id"])

    existing = _row(db, event_id, reg.id, sid)
    if existing is not None:  # a repeat scan: nothing changes, nothing is audited (like event-level check-in)
        return _response(event, session, reg, existing, message="Already checked in to this session", outcome="already_checked_in")

    row = EventSessionAttendance(
        event_id=event_id, registration_id=reg.id, session_id=sid,
        checked_in_at=datetime.utcnow(), checked_in_by=_actor_uuid(current_user),
    )
    try:
        with db.begin_nested():  # a concurrent scan of the same attendee hits the unique constraint here
            db.add(row)
    except IntegrityError:
        existing = _row(db, event_id, reg.id, sid)
        if existing is None:
            raise
        return _response(event, session, reg, existing, message="Already checked in to this session", outcome="already_checked_in")

    _log_audit(
        db, event_id, "session_check_in", None,
        _audit_state(reg, session, row, method=getattr(payload, "method", None)),
        changed_by=_actor_id(current_user), commit=False,
    )
    if commit:
        _commit(db)
    else:
        db.flush()
    return _response(event, session, reg, row, message="Checked in to session", outcome="checked_in")


def uncheck_in_session_service(db: Session, event_id: UUID, session_id: str, payload, current_user: dict | None = None) -> EventSessionAttendanceResponse:
    event, session, reg = _resolve(db, event_id, session_id, payload.registration_id, payload.qr_code)
    row = _row(db, event_id, reg.id, str(session["id"]))
    if row is None:
        raise HTTPException(status_code=400, detail="Cannot undo: registration is not checked in to this session")

    before = _audit_state(reg, session, row)
    db.delete(row)  # the audit row keeps the history; a later check-in creates a fresh row
    _log_audit(
        db, event_id, "session_uncheck_in", before, _audit_state(reg, session, None),
        changed_by=_actor_id(current_user), notes=getattr(payload, "reason", None), commit=False,
    )
    _commit(db)
    return _response(event, session, reg, None, message="Session check-in undone", outcome="unchecked_in")


def check_out_session_service(db: Session, event_id: UUID, session_id: str, payload, current_user: dict | None = None) -> EventSessionAttendanceResponse:
    event, session, reg = _resolve(db, event_id, session_id, payload.registration_id, payload.qr_code)
    if reg.status == "cancelled":
        raise HTTPException(status_code=400, detail="Cannot check-out: registration is cancelled")
    if reg.status == "no_show":
        raise HTTPException(status_code=400, detail="Cannot check-out: registration is marked as no_show")
    row = _row(db, event_id, reg.id, str(session["id"]))
    if row is None:
        raise HTTPException(status_code=400, detail="Cannot check-out: participant has not been checked in to this session")
    if row.checked_out_at is not None:
        return _response(event, session, reg, row, message="Already checked out of this session", outcome="already_checked_out")

    before = _audit_state(reg, session, row)
    row.checked_out_at = datetime.utcnow()
    row.checked_out_by = _actor_uuid(current_user)
    _log_audit(
        db, event_id, "session_check_out", before, _audit_state(reg, session, row),
        changed_by=_actor_id(current_user), commit=False,
    )
    _commit(db)
    return _response(event, session, reg, row, message="Checked out of session", outcome="checked_out")


# ---------------------------------------------------------------------------------------------
# batch check-in
# ---------------------------------------------------------------------------------------------


def _refunded_registration_ids(db: Session, event_id: UUID, regs: list) -> set:
    """Registrations whose paying order was refunded — ``_registration_is_refunded`` for many at once.

    Same pairing as ``_linked_order_for_registration`` (latest order for the event + email placed no later than
    5 minutes after the registration), done from one query for the whole batch instead of one per attendee.
    """
    if not regs:
        return set()
    emails = {(r.participant_email or "").strip().lower() for r in regs}
    orders: dict[str, list] = defaultdict(list)
    for email, created_at, order_status, payment_status in db.execute(
        sa.select(sa.func.lower(EventOrder.participant_email), EventOrder.created_at, EventOrder.status, EventOrder.payment_status)
        .where(EventOrder.event_id == event_id, sa.func.lower(EventOrder.participant_email).in_(emails))
    ):
        orders[email].append((created_at, order_status, payment_status))
    refunded = set()
    for reg in regs:
        cutoff = (reg.created_at or datetime.utcnow()) + _REFUND_PAIRING_GRACE
        candidates = [o for o in orders.get((reg.participant_email or "").strip().lower(), []) if o[0] <= cutoff]
        if candidates:
            _, order_status, payment_status = max(candidates, key=lambda o: o[0])
            if order_status == "refunded" or payment_status == "refunded":
                refunded.add(reg.id)
    return refunded


def _batch_pass(db: Session, event, session: dict, participants: list, current_user, *, safe: bool) -> list[EventSessionBatchCheckInResultItem]:
    """One pass over the batch. Every attendee is validated on its own; a bad one never blocks the rest.

    Fast path (``safe=False``): rows are staged and flushed together (an IntegrityError from a concurrent
    scan aborts the pass and the caller retries it with ``safe=True``). Safe path: each insert sits in its own
    savepoint, so a duplicate becomes "already_checked_in" for that attendee alone.
    """
    event_id, sid = event.id, str(session["id"])
    actor_uuid, actor = _actor_uuid(current_user), _actor_id(current_user)

    ids = {p.registration_id for p in participants if p.registration_id}
    qrs = {p.qr_code for p in participants if not p.registration_id and p.qr_code}
    by_id: dict = {}
    by_qr: dict = {}
    if ids:
        by_id = {r.id: r for r in db.execute(sa.select(EventRegistration).where(EventRegistration.event_id == event_id, EventRegistration.id.in_(ids))).scalars()}
    if qrs:
        by_qr = {r.qr_code: r for r in db.execute(sa.select(EventRegistration).where(EventRegistration.event_id == event_id, EventRegistration.qr_code.in_(qrs))).scalars()}
    known = {r.id: r for r in [*by_id.values(), *by_qr.values()]}
    refunded = _refunded_registration_ids(db, event_id, [r for r in known.values() if r.status != "cancelled"])
    existing = {
        row.registration_id: row
        for row in db.execute(
            sa.select(EventSessionAttendance).where(
                EventSessionAttendance.event_id == event_id, EventSessionAttendance.session_id == sid,
                EventSessionAttendance.registration_id.in_(list(known)),
            )
        ).scalars()
    } if known else {}

    results: list[EventSessionBatchCheckInResultItem] = []
    for item in participants:
        if not item.registration_id and not item.qr_code:
            results.append(EventSessionBatchCheckInResultItem(status="failed", message="registration_id or qr_code is required"))
            continue
        reg = by_id.get(item.registration_id) if item.registration_id else by_qr.get(item.qr_code)
        if reg is None:
            results.append(EventSessionBatchCheckInResultItem(
                registration_id=None, qr_code=item.qr_code, status="failed", message="Registration not found for this event"))
            continue
        identity = dict(registration_id=reg.id, qr_code=item.qr_code, participant_name=reg.participant_name, participant_email=reg.participant_email)
        if reg.status == "cancelled":
            results.append(EventSessionBatchCheckInResultItem(**identity, status="failed", message="Registration is cancelled"))
            continue
        if reg.id in refunded:
            results.append(EventSessionBatchCheckInResultItem(**identity, status="failed", message="Registration was refunded"))
            continue
        row = existing.get(reg.id)
        if row is not None:
            results.append(EventSessionBatchCheckInResultItem(
                **identity, status="already_checked_in", checked_in_at=row.checked_in_at, message="Already checked in to this session"))
            continue
        row = EventSessionAttendance(event_id=event_id, registration_id=reg.id, session_id=sid, checked_in_at=datetime.utcnow(), checked_in_by=actor_uuid)
        if safe:
            try:
                with db.begin_nested():
                    db.add(row)
            except IntegrityError:
                row = _row(db, event_id, reg.id, sid)
                if row is None:
                    raise
                existing[reg.id] = row
                results.append(EventSessionBatchCheckInResultItem(
                    **identity, status="already_checked_in", checked_in_at=row.checked_in_at, message="Already checked in to this session"))
                continue
        else:
            db.add(row)
        existing[reg.id] = row
        _log_audit(db, event_id, "session_check_in", None, _audit_state(reg, session, row, method="batch"), changed_by=actor, commit=False)
        results.append(EventSessionBatchCheckInResultItem(**identity, status="checked_in", checked_in_at=row.checked_in_at, message="Checked in to session"))
    if not safe:
        db.flush()
    return results


def batch_check_in_session_service(db: Session, event_id: UUID, session_id: str, participants: list, current_user: dict | None = None) -> EventSessionBatchCheckInResponse:
    event = _get_event_or_404(db, event_id)
    session = find_session(event, session_id)
    _assert_event_accepts_check_in(event)

    try:
        try:
            results = _batch_pass(db, event, session, participants, current_user, safe=False)
        except IntegrityError:  # a concurrent scan raced one of the inserts: redo the pass insert-by-insert
            db.rollback()
            event = _get_event_or_404(db, event_id)
            results = _batch_pass(db, event, find_session(event, session_id), participants, current_user, safe=True)
        db.commit()
    except Exception:
        db.rollback()
        raise

    failed = sum(1 for r in results if r.status == "failed")
    return EventSessionBatchCheckInResponse(
        event_id=event_id, session_id=str(session["id"]), total=len(results),
        succeeded=len(results) - failed, failed=failed, results=results,
    )


# ---------------------------------------------------------------------------------------------
# reads: per-session counts, per-attendee state
# ---------------------------------------------------------------------------------------------


def _percentage(part: int, whole: int) -> float | None:
    return round(part / whole * 100, 1) if whole > 0 else None


def summarize_session_attendance(db: Session, event) -> list[EventSessionAttendanceSummary]:
    """Per-session counts for the event's current sessions, from two aggregate queries (never per row).

    ``registered_count`` = the event's active registrations (a registration is for the event, not a session).
    ``checked_in_count`` counts attendance rows whose registration is still active, so it can never exceed it.
    Attendance rows of a since-deleted session are kept but not reported.
    """
    sessions = event_sessions_with_ids(event)
    if not sessions:
        return []
    registered = db.execute(
        sa.select(sa.func.count(EventRegistration.id)).where(
            EventRegistration.event_id == event.id, EventRegistration.status.in_(_ACTIVE_STATUSES)
        )
    ).scalar_one()
    checked_in = dict(
        db.execute(
            sa.select(EventSessionAttendance.session_id, sa.func.count(EventSessionAttendance.id))
            .join(EventRegistration, EventRegistration.id == EventSessionAttendance.registration_id)
            .where(EventSessionAttendance.event_id == event.id, EventRegistration.status.in_(_ACTIVE_STATUSES))
            .group_by(EventSessionAttendance.session_id)
        ).all()
    )
    summaries = []
    for session in sessions:
        sid = str(session["id"])
        count = checked_in.get(sid, 0)
        summaries.append(
            EventSessionAttendanceSummary(
                session_id=sid,
                title=session.get("title"),
                session_date=str(session["session_date"]) if session.get("session_date") else None,
                start_time=session.get("start_time"),
                registered_count=registered,
                checked_in_count=count,
                attendance_percentage=_percentage(count, registered),
            )
        )
    return summaries


def attendance_by_session_view(summaries: Iterable[EventSessionAttendanceSummary]) -> dict[str, dict] | None:
    """The legacy ``attendance_by_session`` / report ``by_session`` shape (session id -> {total, attended}),
    now fed by the dedicated attendance records. None when the event has no sessions, as before."""
    summaries = list(summaries)
    if not summaries:
        return None
    return {
        s.session_id: {
            "session_id": s.session_id,
            "title": s.title,
            "total": s.registered_count,
            "attended": s.checked_in_count,
            "attendance_percentage": s.attendance_percentage,
        }
        for s in summaries
    }


def load_attendance_rows(db: Session, event_id: UUID, registration_ids: list) -> dict:
    """registration id -> {session id -> attendance row}, for a page of attendees, in one query."""
    if not registration_ids:
        return {}
    by_registration: dict = defaultdict(dict)
    for row in db.execute(
        sa.select(EventSessionAttendance).where(
            EventSessionAttendance.event_id == event_id, EventSessionAttendance.registration_id.in_(registration_ids)
        )
    ).scalars():
        by_registration[row.registration_id][row.session_id] = row
    return by_registration


def session_states(sessions: list[dict], rows_by_session: dict) -> list[EventSessionAttendanceState]:
    """One entry per current session of the event: checked in or not (rows of deleted sessions are ignored)."""
    states = []
    for session in sessions:
        sid = str(session["id"])
        row = rows_by_session.get(sid)
        states.append(
            EventSessionAttendanceState(
                session_id=sid,
                title=session.get("title"),
                checked_in=row is not None,
                checked_in_at=row.checked_in_at if row is not None else None,
                checked_in_by=row.checked_in_by if row is not None else None,
                checked_out_at=row.checked_out_at if row is not None else None,
            )
        )
    return states
