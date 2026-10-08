"""Issue the certificate to every learner who finished a training but never received one.

A learner could reach 100% in GET /content without a certificate ever being issued (see
tests/test_training_certificate_completion.py for the causes). The certificate page now repairs that for whoever
opens it; this script repairs everyone at once.

A learner is fixed only when their saved progress finishes the training by the rules /content shows (every lesson
done, or every mandatory lesson done), their enrolment is still active and they have no certificate yet. Nobody
else is touched.

No email is sent by default, so a bulk run does not message a long list of learners; add --notify to send the
"certificate ready" notification as well.

Dry run by default (writes nothing):
    python -m scripts.issue_missing_training_certificates
    python -m scripts.issue_missing_training_certificates --apply
    python -m scripts.issue_missing_training_certificates --apply --notify
    python -m scripts.issue_missing_training_certificates --training-id <uuid>
"""
import argparse
import logging
from uuid import UUID

from sqlalchemy import or_

from app.db.database import SessionLocal
from app.models.training_model import TrainingProgress
from app.services.training_service import issue_earned_certificate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="issue the certificates (default: report only)")
    parser.add_argument("--notify", action="store_true", help="also send the certificate-ready notification")
    parser.add_argument("--training-id", type=UUID, help="only this training")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    checked = eligible = failed = 0
    with SessionLocal() as db:
        query = db.query(TrainingProgress.training_id, TrainingProgress.participant_email).filter(
            or_(TrainingProgress.certificate_url.is_(None), TrainingProgress.certificate_url == "")
        )
        if args.training_id:
            query = query.filter(TrainingProgress.training_id == args.training_id)
        for training_id, email in query.all():
            checked += 1
            try:
                if issue_earned_certificate(db, training_id, email, notify=args.notify, dry_run=not args.apply):
                    eligible += 1
                    print(f"{'issued' if args.apply else 'would issue'}: training={training_id} learner={email}")
            except Exception:
                db.rollback()
                failed += 1
                logging.exception("could not process training=%s learner=%s", training_id, email)

    mode = "APPLIED" if args.apply else "DRY RUN (nothing written; add --apply)"
    print(f"{mode}: checked={checked} {'issued' if args.apply else 'would_issue'}={eligible} failed={failed}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
