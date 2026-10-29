"""Meetings for the sessions of an event and the live sessions of a training."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.meeting_model import MeetingLink
from app.models.training_model import Training, TrainingEnrolment, TrainingLiveSession
from app.schemas.training_schema import TrainingLiveSessionCreate
from app.services import meeting_service
from app.services.meeting_providers.base import ProviderError
from event_sql_support import client_for, customer_user, make_registration, staff_user, super_admin_user
from tests.test_meetings import M, connect, env, notified, uuid_of  # noqa: F401


def as_user(env, user):
    return client_for(env.db, user)


@pytest.fixture
def senv(env):
    """The event of `env`, now with two sessions and a time zone, and a training with a live session."""
    env.event.time_zone = "Asia/Kolkata"
    env.event.sessions = [
        {"id": "s1", "title": "Day 1", "session_date": "2026-11-20", "start_time": "10:00", "end_time": "11:30", "meeting_link": None},
        {"id": "s2", "title": "Day 2", "session_date": "2026-11-21", "start_time": None, "end_time": None, "meeting_link": None},
    ]
    env.db.commit()
    for model in (Training, TrainingEnrolment, TrainingLiveSession):
        model.__table__.create(bind=env.db.get_bind(), checkfirst=True)
    training = Training(id=uuid4(), tenant_id=env.tenant, enterprise_id=env.enterprise.id, title="Breathwork", category="Wellness", status="published", delivery_mode="online",
                        time_zone="Asia/Kolkata", sections=[])
    env.db.add(training)
    env.db.commit()
    live = TrainingLiveSession(training_id=training.id, title="Week 1 call", scheduled_at=datetime(2026, 11, 20, 15, 0), duration_minutes="60", meeting_provider="zoom")
    env.db.add(live)
    env.db.commit()
    env.training, env.live = training, live
    return env


# ---------------------------------------------------------------- sessions of an event

def test_a_session_gets_its_own_meeting_at_its_own_time(senv):
    env = senv
    connect(env, "zoom")
    client = as_user(env, env.admin)
    base = f"{M}/events/{env.event.id}/sessions/s1"
    assert client.put(base, json={"provider": "zoom"}).status_code == 200

    body = client.post(f"{base}/generate").json()

    assert body["status"] == "created" and body["kind"] == "event_session" and body["session_id"] == "s1" and body["meeting_link"] == "https://meet.test/zoom/1"
    create = next(c for c in env.providers["zoom"].calls if c[0] == "create")
    assert create[2] == "Day 1" and create[3] == datetime(2026, 11, 20, 4, 30) and create[4] == datetime(2026, 11, 20, 6, 0)          # 10:00-11:30 IST
    env.db.refresh(env.event)
    assert env.event.sessions[0]["meeting_link"] == "https://meet.test/zoom/1" and env.event.sessions[1]["meeting_link"] is None
    assert client.get(f"{M}/events/{env.event.id}").json()["status"] == "none"                                                        # the event's own meeting is separate
    assert env.db.query(MeetingLink).count() == 1 or env.db.query(MeetingLink).filter_by(resource_type="event_session", scope="s1").count() == 1


def test_a_session_without_a_start_time_cannot_be_created_but_can_take_a_pasted_link(senv):
    env = senv
    connect(env, "zoom")
    client = as_user(env, env.admin)
    base = f"{M}/events/{env.event.id}/sessions/s2"
    client.put(base, json={"provider": "zoom"})

    assert client.post(f"{base}/generate").status_code == 422
    pasted = client.put(base, json={"manual_link": "https://example.com/day-2"}).json()
    assert pasted["status"] == "manual" and pasted["meeting_link"] == "https://example.com/day-2"
    env.db.refresh(env.event)
    assert env.event.sessions[1]["meeting_link"] == "https://example.com/day-2"
    assert client.get(f"{M}/events/{env.event.id}/sessions/nope").status_code == 404


def test_the_session_list_shows_every_session(senv):
    env = senv
    client = as_user(env, env.admin)

    items = client.get(f"{M}/events/{env.event.id}/sessions").json()["items"]

    assert [(i["session_id"], i["title"], i["status"]) for i in items] == [("s1", "Day 1", "none"), ("s2", "Day 2", "none")]
    assert as_user(env, env.other).get(f"{M}/events/{env.event.id}/sessions").status_code == 404
    assert as_user(env, env.root).put(f"{M}/events/{env.event.id}/sessions/s1", json={"auto_generate": False}).status_code == 403


def test_publishing_creates_a_session_meeting_only_for_sessions_set_up_for_it(senv):
    env = senv
    connect(env, "zoom")
    client = as_user(env, env.admin)
    client.put(f"{M}/events/{env.event.id}/sessions/s1", json={"provider": "zoom"})
    client.put(f"{M}/settings", json={"default_provider": "zoom"})

    meeting_service.on_event_status(env.db, env.event, "draft", "published")

    states = {i["session_id"]: i["status"] for i in client.get(f"{M}/events/{env.event.id}/sessions").json()["items"]}
    assert states == {"s1": "created", "s2": "none"} and client.get(f"{M}/events/{env.event.id}").json()["status"] == "created"

    meeting_service.on_event_status(env.db, env.event, "published", "cancelled")
    assert {c[1] for c in env.providers["zoom"].calls if c[0] == "delete"} == {"ext-1", "ext-2"}
    env.db.refresh(env.event)
    assert env.event.sessions[0]["meeting_link"] is None and env.event.meeting_link is None


def test_moving_or_deleting_a_session_moves_or_deletes_its_meeting(senv):
    env = senv
    connect(env, "zoom")
    client = as_user(env, env.admin)
    base = f"{M}/events/{env.event.id}/sessions/s1"
    client.put(base, json={"provider": "zoom"})
    client.post(f"{base}/generate")
    sessions = [dict(s) for s in env.event.sessions]
    sessions[0]["start_time"], sessions[0]["end_time"] = "12:00", "13:00"
    env.event.sessions = sessions
    env.db.commit()

    meeting_service.on_event_session_changed(env.db, env.event, "s1")

    update = next(c for c in env.providers["zoom"].calls if c[0] == "update")
    assert update[1] == "ext-1" and update[2] == datetime(2026, 11, 20, 6, 30)
    meeting_service.on_event_session_changed(env.db, env.event, "s1", deleted=True)
    assert ("delete", "ext-1") in env.providers["zoom"].calls and env.db.query(MeetingLink).filter_by(resource_type="event_session").count() == 0


def test_a_customer_sees_the_session_links_with_the_event(senv):
    env = senv
    connect(env, "zoom")
    make_registration(env.db, env.event, "cust@example.com")
    client = as_user(env, env.admin)
    client.put(f"{M}/events/{env.event.id}/sessions/s1", json={"provider": "zoom"})
    client.post(f"{M}/events/{env.event.id}/sessions/s1/generate")

    body = as_user(env, customer_user("cust@example.com")).get(f"{M}/my/events/{env.event.id}").json()

    assert [s["session_id"] for s in body["sessions"]] == ["s1"] and body["sessions"][0]["meeting_link"] == "https://meet.test/zoom/1"
    assert body["sessions"][0]["starts_at"] == "2026-11-20T04:30:00Z" and body["sessions"][0]["can_join_now"] is False and body["link_ready"] is False


# ---------------------------------------------------------------- live sessions of a training

def test_a_live_session_created_on_a_published_training_gets_a_meeting(senv):
    env = senv
    connect(env, "zoom")
    as_user(env, env.admin).put(f"{M}/settings", json={"default_provider": "zoom"})
    from app.services.training_service import create_live_session_service

    result = create_live_session_service(env.db, env.training.id, TrainingLiveSessionCreate(
        title="Week 2 call", scheduled_at=datetime(2026, 11, 27, 15, 0), duration_minutes=45))

    assert result["meeting_link"] == "https://meet.test/zoom/1" and result["meeting_provider"] == "zoom"
    create = next(c for c in env.providers["zoom"].calls if c[0] == "create")
    assert create[3] == datetime(2026, 11, 27, 9, 30) and create[4] == datetime(2026, 11, 27, 10, 15)                                     # 15:00 IST for 45 minutes


def test_a_link_given_when_the_live_session_is_created_is_kept(senv):
    env = senv
    connect(env, "zoom")
    as_user(env, env.admin).put(f"{M}/settings", json={"default_provider": "zoom"})
    from app.services.training_service import create_live_session_service

    result = create_live_session_service(env.db, env.training.id, TrainingLiveSessionCreate(
        title="Guest talk", scheduled_at=datetime(2026, 11, 28, 15, 0), duration_minutes=30, meeting_link="https://example.com/guest", meeting_provider="other"))

    assert result["meeting_link"] == "https://example.com/guest" and not [c for c in env.providers["zoom"].calls if c[0] == "create"]
    assert env.db.query(MeetingLink).filter_by(resource_type="training_session").one().manual_link == "https://example.com/guest"


def test_a_live_session_waits_for_the_training_to_be_published_and_is_withdrawn_with_it(senv):
    env = senv
    connect(env, "zoom")
    client = as_user(env, env.admin)
    client.put(f"{M}/settings", json={"default_provider": "zoom"})
    env.training.status = "draft"
    env.db.commit()
    from app.services.training_service import create_live_session_service

    created = create_live_session_service(env.db, env.training.id, TrainingLiveSessionCreate(title="Later", scheduled_at=datetime(2026, 12, 1, 15, 0), duration_minutes=30))
    assert created["meeting_link"] is None and not [c for c in env.providers["zoom"].calls if c[0] == "create"]

    env.training.status = "published"
    meeting_service.on_training_status(env.db, env.training, "draft", "published")
    env.db.expire_all()
    assert sorted(l.meeting_link for l in env.db.query(TrainingLiveSession).all()) == ["https://meet.test/zoom/1", "https://meet.test/zoom/2"]

    env.training.status = "cancelled"
    meeting_service.on_training_status(env.db, env.training, "published", "cancelled")
    env.db.expire_all()
    assert {c[1] for c in env.providers["zoom"].calls if c[0] == "delete"} == {"ext-1", "ext-2"}
    assert all(l.meeting_link is None for l in env.db.query(TrainingLiveSession).all())


def test_the_business_manages_a_live_session_over_the_api(senv):
    env = senv
    connect(env, "zoom")
    client = as_user(env, env.admin)
    base = f"{M}/trainings/{env.training.id}/sessions"

    listed = client.get(base).json()["items"]
    assert [(i["title"], i["status"], i["virtual"], i["kind"]) for i in listed] == [("Week 1 call", "none", True, "training_session")]
    assert client.put(f"{base}/{env.live.id}", json={"provider": "teams"}).json()["provider"] == "teams"
    connect(env, "teams")
    client = as_user(env, env.admin)
    made = client.post(f"{base}/{env.live.id}/generate").json()
    assert made["status"] == "created" and made["meeting_link"] == "https://meet.test/teams/1"
    env.db.refresh(env.live)
    assert env.live.meeting_link == "https://meet.test/teams/1" and env.live.meeting_provider == "teams"

    pasted = client.put(f"{base}/{env.live.id}", json={"manual_link": "https://example.com/mine"}).json()
    assert pasted["meeting_link"] == "https://example.com/mine"
    assert client.delete(f"{base}/{env.live.id}/generated").json()["meeting_link"] == "https://example.com/mine"                           # a pasted link survives
    assert ("delete", "ext-1") in env.providers["teams"].calls


def test_only_the_owning_business_manages_a_trainings_meetings(senv):
    env = senv
    base = f"{M}/trainings/{env.training.id}/sessions"

    assert as_user(env, env.provider_user).get(base).status_code == 200
    assert as_user(env, env.other).get(base).status_code == 403
    assert as_user(env, customer_user("c@example.com")).get(base).status_code == 403
    assert as_user(env, env.root).get(base).status_code == 200
    assert as_user(env, env.root).post(f"{base}/{env.live.id}/generate").status_code == 403
    assert as_user(env, env.admin).get(f"{base}/{uuid4()}").status_code == 404
    assert as_user(env, env.admin).post(f"{M}/trainings/{uuid4()}/sessions/{env.live.id}/generate").status_code == 404


def test_a_failed_live_session_meeting_is_retried_in_the_background(senv):
    env = senv
    connect(env, "zoom")
    client = as_user(env, env.admin)
    base = f"{M}/trainings/{env.training.id}/sessions/{env.live.id}"
    client.put(base, json={"provider": "zoom"})
    env.providers["zoom"].script = [ProviderError("no", code="provider_rejected")]
    assert client.post(f"{base}/generate").json()["status"] == "failed"
    row = env.db.query(MeetingLink).filter_by(resource_type="training_session").one()
    row.updated_at = datetime.utcnow() - timedelta(minutes=10)
    env.db.commit()

    assert meeting_service.retry_failed(env.db) == {"retried": 1, "created": 1}
    env.db.refresh(env.live)
    assert env.live.meeting_link == "https://meet.test/zoom/1"


def test_an_enrolled_learner_sees_the_live_session_links(senv, monkeypatch):
    env = senv
    connect(env, "zoom")
    env.db.add(TrainingEnrolment(training_id=env.training.id, participant_name="Lee", participant_email="lee@example.com", status="enrolled", qr_code="Q1"))
    env.db.add(TrainingEnrolment(training_id=env.training.id, participant_name="Pat", participant_email="pat@example.com", status="pending_approval", qr_code="Q2"))
    env.db.add(TrainingEnrolment(training_id=env.training.id, participant_name="Old", participant_email="old@example.com", status="enrolled", qr_code="Q3",
                                 access_expires_at=datetime.utcnow() - timedelta(days=1)))
    env.db.commit()
    as_user(env, env.admin).put(f"{M}/trainings/{env.training.id}/sessions/{env.live.id}", json={"provider": "zoom"})
    as_user(env, env.admin).post(f"{M}/trainings/{env.training.id}/sessions/{env.live.id}/generate")
    url = f"{M}/my/trainings/{env.training.id}"

    body = as_user(env, customer_user("Lee@example.com")).get(url).json()

    assert body["title"] == "Breathwork" and len(body["sessions"]) == 1
    item = body["sessions"][0]
    assert item["link_ready"] is True and item["meeting_link"] == "https://meet.test/zoom/1" and item["starts_at"] == "2026-11-20T09:30:00Z" and item["ends_at"] == "2026-11-20T10:30:00Z"
    assert item["join_opens_at"] == "2026-11-20T09:15:00Z" and item["provider_label"] == "Zoom" and item["passcode"] == "123456"
    monkeypatch.setattr(meeting_service, "_now", lambda: datetime(2026, 11, 20, 9, 20))
    assert as_user(env, customer_user("lee@example.com")).get(url).json()["sessions"][0]["can_join_now"] is True
    monkeypatch.setattr(meeting_service, "_now", lambda: datetime(2026, 11, 20, 11, 0))
    assert as_user(env, customer_user("lee@example.com")).get(url).json()["sessions"][0]["can_join_now"] is False
    for who in ("pat@example.com", "old@example.com", "stranger@example.com"):
        assert as_user(env, customer_user(who)).get(url).status_code == 404
    env.training.status = "draft"
    env.db.commit()
    assert as_user(env, customer_user("lee@example.com")).get(url).status_code == 404
    assert as_user(env, None).get(url).status_code == 401
