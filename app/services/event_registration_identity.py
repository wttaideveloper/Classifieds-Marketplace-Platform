"""Who an Event registration belongs to, as a platform user id (``EventRegistration.user_id``).

The same rules as Training enrolments (app/services/training_learner_identity.py), for Event registrations:

* ``stamp_registration`` - when a registration is created: the id is saved only if the authenticated caller *is* the person
  registered (verified identity, not request input). Staff registering someone else, group members and walk-ins stay NULL.
* ``resolve_user_ids`` - for registrations that have none, recovered safely: the same email on the person's other registrations
  that have one, then the identity service's member list of the event's enterprise. Saved on the row.
* ``link_caller_registrations`` - a logged-in person's own request links the older rows that carry *their* verified email.
* ``backfill_user_ids`` - the same inference in bulk, dry run by default (scripts/backfill_event_registration_user_ids.py).
"""
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.services.training_learner_identity import (
    _as_uuid, _email, _from_tenant_listing, caller_verified_email, resolve_enrolling_user_id,
)

logger = logging.getLogger(__name__)


def stamp_registration(reg, current_user: dict | None, access_token: str | None = None) -> None:
    """Set ``reg.user_id`` when the caller is the registrant. Never raises: a registration must not fail over this."""
    if current_user is None:
        return
    try:
        reg.user_id = resolve_enrolling_user_id(current_user, reg.participant_email, access_token)
    except Exception:
        logger.exception("could not tell whose registration this is")


def _from_other_rows(db: Session, email: str) -> UUID | None:
    from app.models.event_aux_models import EventRegistration

    row = (
        db.query(EventRegistration.user_id)
        .filter(func.lower(EventRegistration.participant_email) == email, EventRegistration.user_id.isnot(None))
        .order_by(EventRegistration.created_at.desc()).first()
    )
    return row[0] if row and row[0] else None


def resolve_user_ids(db: Session, rows, event, access_token: str | None = None) -> None:
    """Fill ``user_id`` on every row that lacks it. The member list is loaded once and only if a row needs it."""
    from app.services.group_chat_training import _EmailIndex

    index = _EmailIndex(db, event, access_token)
    changed = False
    for row in rows:
        if row.user_id:
            continue
        email = _email(row.participant_email)
        if "@" not in email:
            continue
        found = _from_other_rows(db, email) or index.get(email)
        if found:
            row.user_id = found
            changed = True
    if changed:
        db.commit()


def link_caller_registrations(db: Session, current_user: dict | None, access_token: str | None = None) -> int:
    """The caller's own request attaches their user id to the older registrations carrying *their* verified email."""
    from app.models.event_aux_models import EventRegistration

    caller_id = _as_uuid((current_user or {}).get("id"))
    email = caller_verified_email(current_user, access_token)
    if caller_id is None or not email:
        return 0
    linked = (
        db.query(EventRegistration)
        .filter(func.lower(EventRegistration.participant_email) == email, EventRegistration.user_id.is_(None),
                func.coalesce(EventRegistration.registration_source, "online") != "walk_in")
        .update({"user_id": caller_id}, synchronize_session=False)
    )
    if linked:
        db.commit()
    return linked


def backfill_user_ids(db: Session, *, apply: bool = False, access_token: str | None = None) -> dict:
    """Resolve user ids for every online registration that has none. Dry run unless ``apply``."""
    from app.models.event_aux_models import EventRegistration
    from app.models.event_model import Event

    counts = {"checked": 0, "resolved": 0, "unresolved": 0, "written": 0}
    events: dict = {}
    rows = db.query(EventRegistration).filter(
        EventRegistration.user_id.is_(None), func.coalesce(EventRegistration.registration_source, "online") != "walk_in").all()
    for row in rows:
        counts["checked"] += 1
        event = events.get(row.event_id)
        if event is None:
            event = events[row.event_id] = db.get(Event, row.event_id)
        email = _email(row.participant_email)
        found = _from_other_rows(db, email) or _from_tenant_listing(db, event, email, access_token)
        if not found:
            counts["unresolved"] += 1
            continue
        counts["resolved"] += 1
        if apply:
            row.user_id = found
            counts["written"] += 1
    if apply and counts["written"]:
        db.commit()
    return counts
