"""Explicitly give existing Base-Price-only Events a default ticket id.

Existing Events are never backfilled automatically. Run this for the Events you want to sell through the
ticket flow:

    python -m scripts.enable_default_ticket_type --event-id <uuid> [--event-id <uuid> ...] [--dry-run]

An Event is enabled only if it is eligible (positive price, valid currency, no configured ticket types, Base
Price enabled in its Event Form). An Event that already has an id is left untouched.
"""
import argparse
from uuid import UUID

from app.db.database import SessionLocal
from app.models.event_model import Event
from app.services.event_service import _may_get_default_ticket, enable_default_ticket_type


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-id", action="append", required=True, type=UUID)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db:
        for event_id in args.event_id:
            event = db.get(Event, event_id)
            if event is None or event.is_deleted:
                print(f"{event_id}: not found")
            elif event.default_ticket_type_id is not None:
                print(f"{event_id}: already has {event.default_ticket_type_id}")
            elif not _may_get_default_ticket(db, event.price, event.currency, event.ticket_types, event.form_configuration_version_id):
                print(f"{event_id}: not eligible")
            elif args.dry_run:
                print(f"{event_id}: would enable")
            else:
                enable_default_ticket_type(db, event)
                print(f"{event_id}: enabled {event.default_ticket_type_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
