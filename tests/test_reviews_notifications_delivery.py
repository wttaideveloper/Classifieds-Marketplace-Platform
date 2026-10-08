"""What the review notifications actually deliver: who gets them, what they say, and that a retry cannot send twice.

The background thread and the network lookups are stubbed; the claim table and the review data are real (SQLite).
"""
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.db.database import Base
from app.models.event_aux_models import EventFeedback
from app.models.notification_model import NotificationEventLog
from app.models.product_model import ProductReview
from app.models.service_model import ServiceReview
from app.models.training_model import TrainingEnrolment, TrainingReview
from app.services import event_notification_service, notification_service, review_notifications as rn
from tests.event_sql_support import make_session
from tests.test_reviews_queue import A, seed

ADMINS = [uuid4(), uuid4()]


@pytest.fixture
def world(monkeypatch):
    db = make_session()
    Base.metadata.create_all(db.bind, tables=[NotificationEventLog.__table__])
    ids = seed(db)

    # the module opens its own session; hand it this one (without closing it)
    @contextmanager
    def session_factory():
        yield db
    monkeypatch.setattr("app.db.database.SessionLocal", session_factory)

    sent = []
    monkeypatch.setattr(notification_service, "create_automatic_notification", lambda _db, **kw: sent.append(kw) or SimpleNamespace(id=uuid4()))
    admin_lookup = {"result": (ADMINS, A)}
    monkeypatch.setattr(event_notification_service, "resolve_enterprise_admin_user_ids",
                        lambda _db, item, access_token=None: admin_lookup["result"])
    yield SimpleNamespace(db=db, ids=ids, sent=sent, admins=admin_lookup)
    db.close()


def review_of(world, module):
    a = world.ids.a
    if module == "training":
        return world.db.query(TrainingReview).filter_by(training_id=a.training).one()
    if module == "product":
        return world.db.query(ProductReview).filter_by(product_id=a.product).one()
    if module == "service":
        return world.db.query(ServiceReview).filter_by(service_id=a.service).one()
    return world.db.query(EventFeedback).filter_by(event_id=a.event, is_review=True).one()


ITEM_KEY = {"training": "training_id", "product": "product_id", "service": "service_id", "event": "event_id"}
ITEM_NAME = {"training": "Training a", "product": "Product a", "service": "Service a", "event": "Event a"}


# --- a new review -> the business's admins -------------------------------------------------------

@pytest.mark.parametrize("module", ["training", "product", "service", "event"])
def test_a_new_review_goes_to_the_enterprise_admins(world, module):
    review = review_of(world, module)

    rn._deliver_submitted(module, str(review.id), None)

    [sent] = world.sent
    assert sent["category"] == "review_submitted" and sent["user_ids"] == ADMINS and sent["tenant_id"] == A
    assert ITEM_NAME[module] in sent["message"] and sent["title"] == "New review"
    meta = sent["metadata"]
    assert meta["category"] == "review_submitted" and meta["entity_type"] == module
    assert meta["review_id"] == str(review.id) and meta[ITEM_KEY[module]] == meta["entity_id"]
    assert meta["rating"] == int(review.rating)
    assert sent["channels"] == ["in_app", "push"]


def test_the_same_review_is_never_announced_twice(world):
    review = review_of(world, "product")

    rn._deliver_submitted("product", str(review.id), None)
    rn._deliver_submitted("product", str(review.id), None)

    assert len(world.sent) == 1


def test_no_admins_found_sends_nothing_and_does_not_burn_the_claim(world):
    review = review_of(world, "product")
    world.admins["result"] = ([], A)
    rn._deliver_submitted("product", str(review.id), None)
    assert world.sent == []

    world.admins["result"] = (ADMINS, A)
    rn._deliver_submitted("product", str(review.id), None)
    assert len(world.sent) == 1  # the earlier attempt did not use up the one allowed delivery


def test_a_failed_delivery_releases_the_claim_so_a_retry_can_succeed(world, monkeypatch):
    review = review_of(world, "product")
    attempts = []

    def flaky(_db, **kw):
        attempts.append(kw)
        if len(attempts) == 1:
            raise RuntimeError("push gateway down")
        return SimpleNamespace(id=uuid4())
    monkeypatch.setattr(notification_service, "create_automatic_notification", flaky)

    rn._deliver_submitted("product", str(review.id), None)
    rn._deliver_submitted("product", str(review.id), None)

    assert len(attempts) == 2


def test_a_missing_review_is_ignored(world):
    assert rn._deliver_submitted("product", str(uuid4()), None) is None
    assert world.sent == []


# --- a decision -> the author --------------------------------------------------------------------

def set_status(world, module, status):
    review = review_of(world, module)
    review.moderation_status = status
    world.db.commit()
    return review


@pytest.mark.parametrize("module", ["product", "service", "event"])
@pytest.mark.parametrize("status,category", [("approved", "review_approved"), ("rejected", "review_rejected")])
def test_the_author_is_told_about_a_decision(world, module, status, category):
    review = set_status(world, module, status)
    author = uuid4()
    review.user_id = author
    world.db.commit()

    rn._deliver_decision(module, str(review.id), status, None)

    [sent] = world.sent
    assert sent["category"] == category and sent["user_ids"] == [author]
    assert ITEM_NAME[module] in sent["message"]
    assert sent["metadata"]["status"] == status and sent["metadata"]["review_id"] == str(review.id)
    assert ("was approved" in sent["message"]) is (status == "approved")
    assert ("was not approved" in sent["message"]) is (status == "rejected")


def test_a_training_author_is_found_through_the_enrolment(world):
    review = set_status(world, "training", "approved")
    learner = uuid4()
    world.db.query(TrainingEnrolment).filter_by(participant_email=review.participant_email).update({"user_id": learner})
    world.db.commit()

    rn._deliver_decision("training", str(review.id), "approved", None)

    [sent] = world.sent
    assert sent["user_ids"] == [learner] and sent["category"] == "review_approved"


def test_an_author_who_cannot_be_identified_is_skipped(world):
    review = set_status(world, "event", "approved")  # the seeded event review has no user id

    rn._deliver_decision("event", str(review.id), "approved", None)

    assert world.sent == []


def test_a_decision_that_was_changed_again_is_not_announced(world):
    review = set_status(world, "product", "rejected")

    rn._deliver_decision("product", str(review.id), "approved", None)  # it is rejected now, not approved

    assert world.sent == []


def test_each_new_decision_is_announced_but_a_retry_of_the_same_one_is_not(world):
    review = set_status(world, "product", "approved")
    first = review.updated_at
    rn._deliver_decision("product", str(review.id), "approved", None)
    rn._deliver_decision("product", str(review.id), "approved", None)   # a retry
    assert len(world.sent) == 1

    review.moderation_status = "rejected"
    world.db.commit()
    rn._deliver_decision("product", str(review.id), "rejected", None)
    review.moderation_status = "approved"
    world.db.commit()
    assert review.updated_at >= first
    rn._deliver_decision("product", str(review.id), "approved", None)    # approved again, later

    assert [s["category"] for s in world.sent] == ["review_approved", "review_rejected", "review_approved"]


def test_pending_is_never_announced(world):
    review = set_status(world, "product", "pending")
    rn.notify_review_decision("product", review.id, "pending")  # returns before dispatching anything
    assert world.sent == []
