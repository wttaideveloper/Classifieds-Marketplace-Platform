"""Explicitly give existing Events a Base Price default ticket id.

Existing Events are never backfilled automatically (no migration writes data). Review first, then enable the
Events you want to sell through the ticket flow:

    # 1. read-only report: which Events are eligible but have no id (changes nothing)
    python -m scripts.enable_default_ticket_type --list-eligible

    # 2. preview, then apply, for specific Events
    python -m scripts.enable_default_ticket_type --event-id <uuid> [--event-id <uuid> ...] --dry-run
    python -m scripts.enable_default_ticket_type --event-id <uuid> [--event-id <uuid> ...]

An Event is enabled only if it is eligible: positive Base Price, valid Event currency, and Base Price enabled in
its Event Form version. Configured Ticket Types (Regular/VIP, ...) do not matter -- the default ticket is an
independent option next to them and is never written into ``ticket_types``. An Event that already has an id is
left untouched, and the id, once assigned, never changes. Orders and registrations are not touched.
"""
import argparse
from uuid import UUID

from app.db.database import SessionLocal
from app.models.event_model import Event
from app.services.event_service import _may_get_default_ticket, enable_default_ticket_type


def _eligible(db, event) -> bool:
    return _may_get_default_ticket(db, event.price, event.currency, event.ticket_types, event.form_configuration_version_id)


def list_eligible(db) -> int:
    """Read-only: Events that would be enabled, so a person can approve the list before running step 2."""
    rows = (
        db.query(Event)
        .filter(Event.is_deleted.is_(False), Event.default_ticket_type_id.is_(None))
        .order_by(Event.created_at)
        .all()
    )
    found = [event for event in rows if _eligible(db, event)]
    for event in found:
        tickets = [t.get("name") for t in (event.ticket_types or []) if isinstance(t, dict)]
        print(f"{event.id}\t{event.status}\t{event.price} {event.currency}\ttickets={tickets}\t{event.title!r}")
    print(f"{len(found)} eligible Event(s) without a default ticket id (nothing was changed)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--event-id", action="append", type=UUID, default=[])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list-eligible", action="store_true", help="read-only report; changes nothing")
    args = parser.parse_args()
    if not args.list_eligible and not args.event_id:
        parser.error("give --event-id (repeatable) or --list-eligible")
    with SessionLocal() as db:
        if args.list_eligible:
            return list_eligible(db)
        for event_id in args.event_id:
            event = db.get(Event, event_id)
            if event is None or event.is_deleted:
                print(f"{event_id}: not found")
            elif event.default_ticket_type_id is not None:
                print(f"{event_id}: already has {event.default_ticket_type_id}")
            elif not _eligible(db, event):
                print(f"{event_id}: not eligible")
            elif args.dry_run:
                print(f"{event_id}: would enable")
            else:
                enable_default_ticket_type(db, event)
                print(f"{event_id}: enabled {event.default_ticket_type_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
