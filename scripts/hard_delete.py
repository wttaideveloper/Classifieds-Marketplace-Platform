"""Permanently (hard) delete one Training or Event and every row that references it.

The app's own DELETE endpoints only soft-delete (is_deleted=True, status="archived"),
so the row stays in the database. Use this script only when the row must be physically
removed (spam cleanup, testing data, GDPR erasure). Children are deleted first in a
FK-safe order, then the parent, inside one transaction. Dry run by default.

Run inside the API container so it uses the same database as the app:
    docker exec -it classifieds-marketplace-platform-api-1 \
        python -m scripts.hard_delete --type training --id <uuid>
    docker exec -it classifieds-marketplace-platform-api-1 \
        python -m scripts.hard_delete --type training --id <uuid> --apply
    docker exec -it classifieds-marketplace-platform-api-1 \
        python -m scripts.hard_delete --type event --id <uuid> --apply

Notes:
  * Take a backup first (see /home/ubuntu/deploy-backups/).
  * event_media rows and the files on disk are NOT touched: event_media has no FK to
    events, so it never blocks the delete and the physical files stay put.
  * Rows that reference the item without a foreign key (e.g. generic notifications or
    review moderation logs) are not deleted and may remain as orphans.
"""
import argparse
import uuid
from collections import OrderedDict

from sqlalchemy import text

from app.db.database import SessionLocal

# (table, column) in the order they must be deleted before the parent row.
# training_lesson_attendance depends on training_enrolments, so it comes first.
TRAINING_CHILDREN = OrderedDict([
    ("training_lesson_attendance", "training_id"),
    ("training_progress", "training_id"),
    ("training_assessment_submissions", "training_id"),
    ("training_assignment_submissions", "training_id"),
    ("training_notification_log", "training_id"),
    ("training_live_sessions", "training_id"),
    ("training_reviews", "training_id"),
    ("training_wishlist_items", "training_id"),
    ("training_waitlist", "training_id"),
    ("training_orders", "training_id"),
    ("training_enrolments", "training_id"),
])
TRAINING_PARENT = ("trainings", "id")

# event_session_attendance / event_registration_options depend on event_registrations
# and event_orders, so they go first. The rest reference only the event.
EVENT_CHILDREN = OrderedDict([
    ("event_session_attendance", "event_id"),
    ("event_registration_options", "event_id"),
    ("event_registrations", "event_id"),
    ("event_waitlist", "event_id"),
    ("event_feedback", "event_id"),
    ("event_orders", "event_id"),
    ("event_audits", "event_id"),
])
EVENT_PARENT = ("events", "id")

TARGETS = {
    "training": (TRAINING_CHILDREN, TRAINING_PARENT),
    "event": (EVENT_CHILDREN, EVENT_PARENT),
}


def _describe(db, parent, parent_col, item_id):
    row = db.execute(
        text(f"SELECT * FROM {parent} WHERE {parent_col} = :id"), {"id": item_id}
    ).mappings().first()
    if row is None:
        return None
    label = next(
        (row[k] for k in ("title", "name", "slug") if k in row and row[k]),
        str(row.get(parent_col)),
    )
    return f"{parent}: {label} | status={row.get('status')} | is_deleted={row.get('is_deleted')}"


def _plan_counts(db, children, parent, parent_col, item_id):
    counts = OrderedDict()
    total = 0
    for table, column in children.items():
        n = db.execute(
            text(f"SELECT count(*) FROM {table} WHERE {column} = :id"), {"id": item_id}
        ).scalar_one()
        if n:
            counts[table] = n
            total += n
    return counts, total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--type", required=True, choices=sorted(TARGETS), help="training or event")
    parser.add_argument("--id", required=True, help="UUID of the training/event to delete")
    parser.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    args = parser.parse_args()

    try:
        item_id = uuid.UUID(args.id)
    except ValueError:
        parser.error(f"--id is not a valid UUID: {args.id}")

    children, (parent, parent_col) = TARGETS[args.type]

    with SessionLocal() as db:
        described = _describe(db, parent, parent_col, item_id)
        if described is None:
            print(f"NOT FOUND: no {args.type} with id={item_id}")
            return 1
        print(described)

        counts, total = _plan_counts(db, children, parent, parent_col, item_id)
        print(f"child rows referencing this {args.type}:")
        for table, n in counts.items():
            print(f"  {table}: {n}")
        print(f"  (total {total})")

        if not args.apply:
            print("\nDRY RUN — nothing deleted. Re-run with --apply to delete.")
            return 0

        try:
            for table, column in children.items():
                db.execute(text(f"DELETE FROM {table} WHERE {column} = :id"), {"id": item_id})
            db.execute(text(f"DELETE FROM {parent} WHERE {parent_col} = :id"), {"id": item_id})
            db.commit()
        except Exception as exc:  # FK violation / DB error: leave the database untouched
            db.rollback()
            print(f"\nROLLED BACK, nothing deleted: {type(exc).__name__}: {exc}")
            return 2

        print(f"\nDELETED {args.type} {item_id} and {total} child rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
