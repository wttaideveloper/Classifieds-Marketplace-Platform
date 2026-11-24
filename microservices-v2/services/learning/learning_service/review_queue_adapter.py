"""Learning's part of the review moderation queue (training reviews), copied from app/services/review_admin_service.py."""
import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.orm import joinedload

from learning_service.services.review_access import can_view_all_tenants
from mskit.review_policy import public_name
from mskit.review_queue import Adapter


class TrainingReviews(Adapter):
    modules = ("training",)

    def scoped_query(self, db, module, access):
        from learning_service.models.enterprise_model import Enterprise
        from learning_service.models.training_model import Training, TrainingReview

        t = access.tenant_id
        everyone = can_view_all_tenants(access)
        q = db.query(TrainingReview, Training).join(Training, Training.id == TrainingReview.training_id)
        q = q.options(joinedload(Training.enterprise)).filter(Training.is_deleted.is_(False))
        if not everyone:
            q = q.outerjoin(Enterprise, Training.enterprise_id == Enterprise.id).filter(sa.or_(
                sa.and_(Enterprise.tenant_id == t, sa.or_(Training.tenant_id.is_(None), Training.tenant_id == t)),
                sa.and_(Enterprise.id.is_(None), Training.tenant_id == t),
            ))
        return q, TrainingReview, Training, TrainingReview.training_id, Training.title

    def names(self, db, module, rows):
        from learning_service.models.training_model import TrainingEnrolment

        found: dict = {}
        for review, _ in rows:
            for e in db.query(TrainingEnrolment).filter(
                TrainingEnrolment.training_id == review.training_id,
                TrainingEnrolment.participant_email == review.participant_email,
            ).order_by(TrainingEnrolment.created_at.desc()).all():
                name = public_name(e.participant_name)
                if name:
                    found[(review.training_id, review.participant_email)] = name
                    break
        return found

    def moderate(self, db, module, review_id, action, access, *, access_token, actor, reason):
        from learning_service.models.training_model import TrainingReview
        from learning_service.services.training_service import moderate_training_review_service

        review = db.get(TrainingReview, review_id)
        if review is None:
            raise HTTPException(status_code=404, detail="Review not found")
        moderate_training_review_service(db, review.training_id, review_id, action, access, access_token=access_token, actor=actor, reason=reason)

    def delete(self, db, module, review_id, current_user, *, access_token, reason):
        from learning_service.models.training_model import TrainingReview
        from learning_service.services.training_service import delete_training_review_service

        review = db.get(TrainingReview, review_id)
        if review is None:
            raise HTTPException(status_code=404, detail="Review not found")
        return delete_training_review_service(db, review.training_id, review_id, current_user, access_token=access_token, reason=reason)
