"""Fill TrainingEnrolment.user_id / TrainingWaitlist.user_id for rows saved before user ids were recorded.

Without a user id, training_enrollment_accepted / _rejected cannot reach the learner's notification feed.

The id is recovered only from sources that do not depend on anything a client sent:
  1. the same person's other enrolment / waitlist rows (same email) that already carry a user id;
  2. the Auth service's member list of the training's tenant (needs INVIGORATE_AUTH_BASE_URL and
     INVIGORATE_INTERNAL_API_KEY), matched on email.
Learners who belong to no tenant and have no other row cannot be resolved here; they are linked
automatically the next time they open GET /api/v1/trainings/my/enrolments while logged in.

Dry run by default (writes nothing):
    python -m scripts.backfill_training_enrolment_user_ids
    python -m scripts.backfill_training_enrolment_user_ids --apply
"""
import argparse
import logging

from app.db.database import SessionLocal
from app.services.training_learner_identity import backfill_user_ids


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
