"""Fill EventRegistration.user_id for registrations saved before it existed (Event Group Chat needs it to invite people).

The id is recovered only from sources that do not depend on anything a client sent:
  1. the same person's other registrations (same email) that already carry a user id;
  2. the identity service's member list of the event's tenant, matched on email.
Walk-in registrations are never linked (they are not part of Event Group Chat). A registrant who belongs to no tenant is
linked the next time they open GET /api/v1/events/my/registrations while logged in.

Dry run by default (writes nothing):
    python -m scripts.backfill_event_registration_user_ids
    python -m scripts.backfill_event_registration_user_ids --apply
"""
import argparse
import logging

from app.db.database import SessionLocal
from app.services.event_registration_identity import backfill_user_ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the resolved ids (default: report only)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    with SessionLocal() as db:
        counts = backfill_user_ids(db, apply=args.apply)

    mode = "APPLIED" if args.apply else "DRY RUN (nothing written; add --apply)"
    print(f"{mode}: checked={counts['checked']} resolved={counts['resolved']} "
          f"unresolved={counts['unresolved']} written={counts['written']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
