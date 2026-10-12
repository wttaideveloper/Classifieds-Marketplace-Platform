"""Permanently (hard) delete many Trainings and/or Events, optionally scoped to an enterprise/tenant.

Trainings and Events have no creator column; the only ownership signal is enterprise_id / tenant_id.
Use this to wipe an enterprise's (or the whole) catalog. Children are deleted first in FK-safe order,
then the parents, all in one transaction. Dry run by default.

    # preview everything (nothing deleted)
    docker exec -it classifieds-marketplace-platform-api-1 \
        python -m scripts.wipe_trainings_events --type both

    # scope to one enterprise / tenant
    docker exec -it classifieds-marketplace-platform-api-1 \
        python -m scripts.wipe_trainings_events --type training --enterprise-id <uuid>

    # actually delete
    docker exec -it classifieds-marketplace-platform-api-1 \
        python -m scripts.wipe_trainings_events --type both --apply

Notes:
  * Take a backup first (see /home/ubuntu/deploy-backups/).
  * Soft-deleted rows are included (this is a physical wipe, not the archive the API does).
  * Deleting a training also removes its enrolments, orders, reviews, progress, attendance,
    certificates data, waitlist and wishlist rows; deleting an event removes registrations,
    orders, feedback, waitlist and audits. This is irreversible.
  * event_media rows/files are not touched (no FK to events).
  * Once trainings/events are gone, their now-unreferenced retired form configurations can be
    deleted too (see the SQL for training/event form configurations).
"""
import argparse
from collections import OrderedDict

from sqlalchemy import text

from app.db.database import SessionLocal
from scripts.hard_delete import EVENT_CHILDREN, EVENT_PARENT, TRAINING_CHILDREN, TRAINING_PARENT

PLANS = {
    "training": (TRAINING_CHILDREN, TRAINING_PARENT, "trainings"),
    "event": (EVENT_CHILDREN, EVENT_PARENT, "events"),
}


def _filter(enterprise_id, tenant_id):
    clauses, params = [], {}
    if enterprise_id:
        clauses.append("enterprise_id = :enterprise_id")
        params["enterprise_id"] = enterprise_id
    if tenant_id:
        clauses.append("tenant_id = :tenant_id")
        params["tenant_id"] = tenant_id
    return (" AND ".join(clauses) if clauses else "TRUE"), params


def _wipe(db, kind, enterprise_id, tenant_id, apply):
    children, (parent, parent_col), _parent_table = PLANS[kind]
    where, params = _filter(enterprise_id, tenant_id)
    sub = f"SELECT id FROM {parent} WHERE {where}"

    parent_n = db.execute(text(f"SELECT count(*) FROM {parent} WHERE {where}"), params).scalar_one()
    print(f"\n[{kind}] parent rows matching filter: {parent_n}")
    counts = OrderedDict()
    total = 0
    for table, column in children.items():
        n = db.execute(text(f"SELECT count(*) FROM {table} WHERE {column} IN ({sub})"), params).scalar_one()
        if n:
            counts[table] = n
            total += n
    for table, n in counts.items():
        print(f"  {table}: {n}")
    print(f"  (child rows total {total})")

    if not apply:
        return parent_n, total

    for table, column in children.items():
        db.execute(text(f"DELETE FROM {table} WHERE {column} IN ({sub})"), params)
    db.execute(text(f"DELETE FROM {parent} WHERE {where}"), params)
    return parent_n, total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--type", choices=["training", "event", "both"], default="both")
    parser.add_argument("--enterprise-id", help="only rows of this enterprise (default: all)")
    parser.add_argument("--tenant-id", help="only rows of this tenant (default: all)")
    parser.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    args = parser.parse_args()

    kinds = ["training", "event"] if args.type == "both" else [args.type]

    with SessionLocal() as db:
        if not args.apply:
            for kind in kinds:
                _wipe(db, kind, args.enterprise_id, args.tenant_id, apply=False)
            print("\nDRY RUN — nothing deleted. Re-run with --apply to delete.")
            return 0

        grand_parent = grand_child = 0
        try:
            counts = []
            for kind in kinds:
                p, c = _wipe(db, kind, args.enterprise_id, args.tenant_id, apply=True)
                counts.append((kind, p, c))
                grand_parent += p
                grand_child += c
            db.commit()
        except Exception as exc:  # FK violation / DB error: leave the database untouched
            db.rollback()
            print(f"\nROLLED BACK, nothing deleted: {type(exc).__name__}: {exc}")
            return 2

        for kind, p, c in counts:
            print(f"DELETED {p} {kind}(s) and {c} child rows")
        print(f"TOTAL: {grand_parent} parent rows, {grand_child} child rows deleted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
