"""Event feedback forms: who can read them (list and report), what each role gets when denied or when there is none, and the
event_feedback_submitted notification to the event's Enterprise Admins and the Super Admins."""
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.event_aux_models import EventFeedback
from app.models.notification_model import NotificationEventLog
from app.services import event_notification_service, notification_service, review_notifications as rn
from tests.event_sql_support import (
    API, client_for, customer_user, make_enterprise, make_event, make_registration, make_session, reset_overrides,
    staff_user, super_admin_user,
)

TENANT, OTHER = uuid4(), uuid4()
ENTERPRISE_ADMINS, SUPER_ADMINS = [uuid4(), uuid4()], [uuid4(), uuid4()]
ANSWERS = {"overall": "Excellent", "would_recommend": True, "topics": ["Yoga", "Breathing"]}


@pytest.fixture
def world(monkeypatch):
    db = make_session()
    NotificationEventLog.__table__.create(db.bind, checkfirst=True)
    enterprise = make_enterprise(db, TENANT)
    event = make_event(db, TENANT, enterprise=enterprise, title="Wellness Summit")
    asha = customer_user("asha@example.com")
    make_registration(db, event, "asha@example.com")
    sent = []

    @contextmanager
    def session_factory():
        yield db

    monkeypatch.setattr("app.db.database.SessionLocal", session_factory)
    monkeypatch.setattr(rn, "_dispatch", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(notification_service, "create_automatic_notification", lambda _db, **kw: sent.append(kw) or SimpleNamespace(id=uuid4()))
    monkeypatch.setattr(event_notification_service, "resolve_platform_admin_user_ids", lambda token=None: list(SUPER_ADMINS))
    monkeypatch.setattr(event_notification_service, "resolve_enterprise_admin_user_ids", lambda _db, item, access_token=None: (list(ENTERPRISE_ADMINS), TENANT))
    yield SimpleNamespace(db=db, event=event, asha=asha, sent=sent, enterprise=enterprise)
    reset_overrides()
    db.close()


def send(world, user=None, **body):
    return client_for(world.db, user or world.asha).post(f"{API}/{world.event.id}/feedback", json=body)


def listing(world, user):
    return client_for(world.db, user).get(f"{API}/{world.event.id}/feedback")


def report(world, user, **params):
    return client_for(world.db, user).get(f"{API}/{world.event.id}/reports", params={"type": "feedback", "format": "json", **params})


# --- who can read the list and the report ------------------------------------------------------------------------------

@pytest.mark.parametrize("make_user", [
    lambda: staff_user(TENANT, "admin"), lambda: staff_user(TENANT, "provider"), lambda: super_admin_user(),
])
def test_the_owning_admin_the_provider_and_a_super_admin_can_read_the_list_and_the_feedback_report(world, make_user):
    send(world, rating="5", comment="Great", answers=ANSWERS)
    user = make_user()

    assert listing(world, user).status_code == 200 and len(listing(world, user).json()) == 1
    assert report(world, user).status_code == 200 and report(world, user).json()["data"]["total_feedbacks"] == 1


def test_a_super_admin_reads_the_feedback_report_only_not_the_other_reports(world):
    root = super_admin_user()

    for kind in ("registration", "attendance", "revenue", "cancellation"):
        resp = report(world, root, type=kind)
        assert resp.status_code == 403 and resp.json() == {"detail": "A Super Admin can read the feedback report only"}, kind
    assert client_for(world.db, staff_user(TENANT, "admin")).get(f"{API}/{world.event.id}/reports", params={"type": "registration"}).status_code == 200


def test_everyone_else_is_refused_with_the_reason(world):
    send(world, comment="Great")
    cases = {
        "a customer": (world.asha, 403, "Not authorized"),
        "staff of another business": (staff_user(OTHER, "admin"), 403, None),
        "a provider of another business": (staff_user(OTHER, "provider"), 403, None),
        "an inactive Super Admin": ({**super_admin_user(), "status": "disabled"}, 403, None),
    }
    for label, (user, code, detail) in cases.items():
        for resp in (listing(world, user), report(world, user)):
            assert resp.status_code == code, label
            assert "detail" in resp.json() and (detail is None or resp.json()["detail"] == detail), label
            assert "Great" not in resp.text


def test_not_logged_in_is_401_and_an_unknown_event_is_404(world):
    assert client_for(world.db, None).get(f"{API}/{world.event.id}/feedback").status_code == 401
    assert client_for(world.db, None).get(f"{API}/{world.event.id}/reports", params={"type": "feedback"}).status_code == 401
    root = client_for(world.db, super_admin_user())
    assert root.get(f"{API}/{uuid4()}/feedback").status_code == 404
    assert root.get(f"{API}/{uuid4()}/feedback").json() == {"detail": "Event not found"}


# --- what they get ---------------------------------------------------------------------------------------------------------

def test_an_event_with_no_feedback_gives_an_empty_list_and_an_empty_report(world):
    admin = staff_user(TENANT, "admin")

    assert listing(world, admin).status_code == 200 and listing(world, admin).json() == []
    assert report(world, admin).json() == {
        "event_id": str(world.event.id), "type": "feedback",
        "data": {"total_feedbacks": 0, "total_reviews": 0, "approved_reviews": 0, "average_rating": None, "feedbacks": []},
    }


def test_the_list_has_the_attendee_name_the_comment_and_the_questionnaire_answers(world):
    send(world, form_id="post-event", rating="5", comment="Great sessions", answers=ANSWERS)

    [item] = listing(world, staff_user(TENANT, "admin")).json()

    assert item["participant_name"] == "asha" and item["participant_email"] == "asha@example.com"
    assert item["answers"] == ANSWERS and item["comment"] == "Great sessions" and item["rating"] == "5"
    assert item["form_id"] == "post-event" and item["event_id"] == str(world.event.id) and item["is_review"] is False
    assert set(item) == {"id", "event_id", "participant_name", "participant_email", "form_id", "answers", "rating", "comment", "is_review", "created_at"}


def test_the_report_carries_the_answers_too_and_counts_reviews_separately(world):
    send(world, rating="4", comment="Good", answers=ANSWERS)
    client_for(world.db, world.asha).post(f"{API}/{world.event.id}/reviews", json={"rating": 5, "comment": "Loved it"})

    data = report(world, staff_user(TENANT, "admin")).json()["data"]

    assert data["total_feedbacks"] == 1 and data["total_reviews"] == 1 and data["approved_reviews"] == 1 and data["average_rating"] == 5.0
    [row] = data["feedbacks"]
    assert row["answers"] == ANSWERS and row["comment"] == "Good" and row["participant_name"] == "asha"
    assert set(row) == {"id", "participant_name", "form_id", "answers", "rating", "comment", "created_at"}


def test_the_attendee_is_the_logged_in_user_not_the_email_in_the_body(world):
    send(world, participant_email="someone.else@example.com", comment="As someone else")

    [item] = listing(world, staff_user(TENANT, "admin")).json()

    assert item["participant_email"] == "asha@example.com"
    assert world.db.query(EventFeedback).filter_by(participant_email="someone.else@example.com").count() == 0


# --- notifications -----------------------------------------------------------------------------------------------------------

def feedback_notices(world):
    return [n for n in world.sent if n["category"] == "event_feedback_submitted"]


def test_the_enterprise_admins_and_the_super_admins_are_told_with_the_text_the_name_and_the_event(world):
    send(world, form_id="post-event", rating="5", comment="Great sessions", answers=ANSWERS)

    owners, platform = feedback_notices(world)

    assert owners["user_ids"] == ENTERPRISE_ADMINS and owners["tenant_id"] == TENANT
    assert platform["user_ids"] == SUPER_ADMINS and platform["tenant_id"] is None
    for notice in (owners, platform):
        assert notice["title"] == "New event feedback" and notice["channels"] == ["in_app", "push"]
        assert notice["message"] == 'asha sent feedback on "Wellness Summit".' and "Great sessions" not in notice["message"]
        meta = notice["metadata"]
        assert meta["category"] == "event_feedback_submitted" and meta["comment"] == "Great sessions"
        assert meta["event_id"] == meta["item_id"] == meta["entity_id"] == str(world.event.id) and meta["entity_type"] == "event"
        assert meta["event_title"] == meta["item_name"] == "Wellness Summit" and meta["attendee_name"] == "asha"
        assert meta["rating"] == 5 and meta["form_id"] == "post-event" and meta["feedback_id"]
        assert meta["submitted_at"] and "answers" not in meta and "@" not in str(meta)


def test_the_comment_is_left_out_when_there_is_none_and_the_name_when_unknown(world):
    stranger = customer_user("walkin@example.com")           # not registered, so no name on file
    send(world, stranger, rating="3")

    [owners, _platform] = feedback_notices(world)

    assert "comment" not in owners["metadata"] and "attendee_name" not in owners["metadata"]
    assert owners["message"] == 'An attendee sent feedback on "Wellness Summit".'


def test_a_super_admin_who_also_owns_the_business_is_told_once(world, monkeypatch):
    monkeypatch.setattr(event_notification_service, "resolve_enterprise_admin_user_ids", lambda _db, item, access_token=None: ([SUPER_ADMINS[0], *ENTERPRISE_ADMINS], TENANT))

    send(world, comment="Great")

    owners, platform = feedback_notices(world)
    assert SUPER_ADMINS[0] in owners["user_ids"] and platform["user_ids"] == [SUPER_ADMINS[1]]


def test_each_feedback_is_announced_once(world):
    send(world, comment="First")
    send(world, comment="Second")

    assert [n["metadata"]["comment"] for n in feedback_notices(world)] == ["First", "First", "Second", "Second"]
    feedback = world.db.query(EventFeedback).filter_by(is_review=False).first()
    from app.services import event_feedback_notifications as efn

    efn._deliver_feedback_submitted(str(feedback.id), None)              # the same one again: already delivered
    assert len(feedback_notices(world)) == 4


def test_reviews_are_not_announced_as_feedback_and_a_failing_notification_never_breaks_the_call(world, monkeypatch):
    client_for(world.db, world.asha).post(f"{API}/{world.event.id}/reviews", json={"rating": 5, "comment": "Loved it"})
    assert feedback_notices(world) == []                      # a review has its own review_submitted notice

    monkeypatch.setattr(rn, "_dispatch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("queue down")))
    assert send(world, comment="Still saved").status_code == 201
    assert world.db.query(EventFeedback).filter_by(is_review=False).count() == 1


def test_the_push_payload_leaves_the_comment_out():
    from app.services import notification_delivery_service

    assert "comment" in notification_delivery_service.PUSH_OMITTED_METADATA_KEYS
