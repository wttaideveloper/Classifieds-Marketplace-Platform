"""Events' part of the review moderation queue (event reviews), copied from app/services/review_admin_service.py."""
import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.orm import joinedload

from events_service.services.review_access import assert_can_moderate, can_view_all_tenants
from mskit.review_policy import public_name
from mskit.review_queue import Adapter


class EventReviews(Adapter):
    modules = ("event",)

    def scoped_query(self, db, module, access):
        from events_service.models.event_aux_models import EventFeedback
        from events_service.models.event_model import Event
        from events_service.repository.event_repo import event_tenant_clause

        t = access.tenant_id
        everyone = can_view_all_tenants(access)
        q = db.query(EventFeedback, Event).join(Event, Event.id == EventFeedback.event_id)
        q = q.options(joinedload(Event.enterprise)).filter(EventFeedback.is_review.is_(True))
        if hasattr(Event, "is_deleted"):
            q = q.filter(Event.is_deleted.is_(False))
        if not everyone:
            clause = event_tenant_clause(t)
            q = q.filter(clause) if clause is not None else q.filter(sa.false())
        return q, EventFeedback, Event, EventFeedback.event_id, Event.title

    def names(self, db, module, rows):
        from events_service.models.event_aux_models import EventRegistration

        found = {}
        for review, _ in rows:
            email = (review.participant_email or "").strip().lower()
            if not email:
                continue
            reg = db.query(EventRegistration).filter(
                EventRegistration.event_id == review.event_id,
                sa.func.lower(EventRegistration.participant_email) == email,
            ).order_by(EventRegistration.created_at.asc()).first()
            name = public_name(reg.participant_name) if reg else None
            if name:
                found[(review.event_id, email)] = name
        return found

    def moderate(self, db, module, review_id, action, access, *, access_token, actor, reason):
        from events_service.models.event_aux_models import EventFeedback
        from events_service.repository.event_repo import event_owner_tenant_id, get_event_by_id
        from events_service.services.event_service import moderate_review_service

        review = db.get(EventFeedback, review_id)
        if review is None or not review.is_review:
            raise HTTPException(status_code=404, detail="Review not found")
        event = get_event_by_id(db, review.event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="Review not found")
        assert_can_moderate(access, event_owner_tenant_id(event))
        moderate_review_service(db, review_id, action, event_id=review.event_id, access_token=access_token, actor=actor, reason=reason)

    def delete(self, db, module, review_id, current_user, *, access_token, reason):
        from events_service.models.event_aux_models import EventFeedback
        from events_service.services.event_service import delete_event_review_service

        review = db.get(EventFeedback, review_id)
        if review is None or not review.is_review:
            raise HTTPException(status_code=404, detail="Review not found")
        return delete_event_review_service(db, review.event_id, review_id, current_user, access_token=access_token, reason=reason)
