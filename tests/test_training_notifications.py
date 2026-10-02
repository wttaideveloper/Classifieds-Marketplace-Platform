"""Training-module notification triggers.

In-app/push:  enrolment approved, new training published, certificate earned, announcements,
              admin answers a question, day-before reminder, final-day notice.
Email:        enrolment approved, certificate earned (plus announcements whose channel asks for it).
"""
import contextlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.database import Base
from app.models.enterprise_model import Enterprise
from app.models.training_model import (
    Training, TrainingEnrolment, TrainingLiveSession, TrainingNotificationLog, TrainingOrder,
    TrainingProgress, TrainingWaitlist,
)
from app.services import training_notifications as tn
from app.services import training_service as service

TID = UUID("5a5a5a5a-1111-4222-8333-444455556666")
TENANT = uuid4()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[m.__table__ for m in (
        Enterprise, Training, TrainingEnrolment, TrainingProgress, TrainingNotificationLog,
        TrainingLiveSession, TrainingOrder, TrainingWaitlist,
    )])
    sessions = sessionmaker(bind=engine)
    delivered = []

    def record(**kwargs):
        delivered.append(kwargs)
        return {"in_app": kwargs.get("in_app", True), "email": bool(kwargs.get("send_email"))}

    monkeypatch.setattr(tn, "_dispatch", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(tn, "deliver_to_participant", record)
    yield sessions, delivered
    engine.dispose()


def add_training(db, **overrides):
    values = dict(
        id=TID, enterprise_id=uuid4(), tenant_id=TENANT, title="Ergonomics 101", category="Wellness",
        status="published", delivery_mode="physical", venue="Studio B", time_zone="Asia/Kolkata",
        start_date=datetime(2026, 10, 5, 10, 0), end_date=datetime(2026, 10, 7, 17, 0),
        sections=[{"id": "s1", "type": "section", "lessons": [
            {"id": "l1", "type": "topic", "title": "One"}, {"id": "l2", "type": "topic", "title": "Two"}]}],
    )
    values.update(overrides)
    t = Training(**values)
    db.add(t)
    db.commit()
    return t


def add_enrolment(db, email, status="enrolled", user_id=None, name=None):
    e = TrainingEnrolment(
        training_id=TID, participant_name=name or email.split("@")[0], participant_email=email,
        status=status, user_id=user_id, qr_code=uuid4().hex[:12],
    )
    db.add(e)
    db.commit()
    return e


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


# --- 6. day-before reminder -------------------------------------------------------------

def test_day_before_reminder_goes_to_enrolled_learner(env):
    sessions, delivered = env
    uid = uuid4()
    with sessions() as db:
        add_training(db)
        add_enrolment(db, "a@example.com", user_id=uid, name="Asha")
        result = tn.send_due_training_reminders(db, utc(2026, 10, 4, 4, 0))  # 09:30 IST on Oct 4

    assert result[tn.KIND_DAY_BEFORE] == 1
    [n] = delivered
    assert n["category"] == "training_reminder"
    assert n["title"] == "Reminder: Ergonomics 101 starts tomorrow"
    assert "05 Oct 2026" in n["message"] and "Studio B" in n["message"] and "Asha" in n["message"]
    assert n["user_id"] == uid and n["email"] == "a@example.com"
    assert not n.get("send_email")  # reminders are in-app/push only


def test_reminder_waits_until_9am_in_the_training_time_zone(env):
    sessions, delivered = env
    with sessions() as db:
        add_training(db)
        add_enrolment(db, "a@example.com")
        tn.send_due_training_reminders(db, utc(2026, 10, 4, 3, 0))  # 08:30 IST — too early
    assert delivered == []


def test_day_before_is_judged_in_training_time_zone_not_utc(env):
    sessions, delivered = env
    with sessions() as db:
        add_training(db, time_zone="America/Los_Angeles")
        add_enrolment(db, "a@example.com")
        # 02:00 UTC on Oct 5 is 19:00 PDT on Oct 4 — "tomorrow" for a learner there, even though
        # the UTC calendar already says Oct 5 (the start day itself).
        tn.send_due_training_reminders(db, utc(2026, 10, 5, 2, 0))
    assert [n["category"] for n in delivered] == ["training_reminder"]


def test_reminder_not_sent_two_days_out(env):
    sessions, delivered = env
    with sessions() as db:
        add_training(db)
        add_enrolment(db, "a@example.com")
        tn.send_due_training_reminders(db, utc(2026, 10, 3, 6, 0))
    assert delivered == []


# --- 7. final-day notice ----------------------------------------------------------------

def test_final_day_notice_on_the_last_day(env):
    sessions, delivered = env
    with sessions() as db:
        add_training(db)
        add_enrolment(db, "a@example.com")
        result = tn.send_due_training_reminders(db, utc(2026, 10, 7, 4, 0))  # 09:30 IST on Oct 7
    assert result[tn.KIND_FINAL_DAY] == 1
    [n] = delivered
    assert n["category"] == "training_final_day"
    assert n["title"] == "Final day: Ergonomics 101"
    assert not n.get("send_email")


def test_single_day_training_final_day_falls_back_to_start_date(env):
    sessions, delivered = env
    with sessions() as db:
        add_training(db, end_date=None)
        add_enrolment(db, "a@example.com")
        tn.send_due_training_reminders(db, utc(2026, 10, 5, 4, 0))  # the start day itself
    assert [n["category"] for n in delivered] == ["training_final_day"]


def test_no_final_day_notice_mid_training(env):
    sessions, delivered = env
    with sessions() as db:
        add_training(db)
        add_enrolment(db, "a@example.com")
        tn.send_due_training_reminders(db, utc(2026, 10, 6, 4, 0))
    assert delivered == []


# --- reminder safety: once only, right audience, right trainings -------------------------

def test_reminder_is_sent_once_even_if_the_scheduler_ticks_again(env):
    sessions, delivered = env
    with sessions() as db:
        add_training(db)
        add_enrolment(db, "a@example.com")
        first = tn.send_due_training_reminders(db, utc(2026, 10, 4, 4, 0))
        second = tn.send_due_training_reminders(db, utc(2026, 10, 4, 4, 15))
        third = tn.send_due_training_reminders(db, utc(2026, 10, 4, 10, 0))
    assert first[tn.KIND_DAY_BEFORE] == 1 and second[tn.KIND_DAY_BEFORE] == 0 and third[tn.KIND_DAY_BEFORE] == 0
    assert len(delivered) == 1


def test_reminders_only_reach_learners_who_are_actually_in(env):
    sessions, delivered = env
    with sessions() as db:
        add_training(db)
        add_enrolment(db, "in@example.com", status="enrolled")
        add_enrolment(db, "attended@example.com", status="attended")
        for i, status in enumerate(("pending_approval", "cancelled", "rejected", "waitlisted")):
            add_enrolment(db, f"out{i}@example.com", status=status)
        tn.send_due_training_reminders(db, utc(2026, 10, 4, 4, 0))
    assert sorted(n["email"] for n in delivered) == ["attended@example.com", "in@example.com"]


@pytest.mark.parametrize("overrides", [{"status": "draft"}, {"status": "cancelled"}, {"is_deleted": True}])
def test_reminders_skip_unpublished_or_deleted_trainings(env, overrides):
    sessions, delivered = env
    with sessions() as db:
        add_training(db, **overrides)
        add_enrolment(db, "a@example.com")
        tn.send_due_training_reminders(db, utc(2026, 10, 4, 4, 0))
    assert delivered == []


def test_training_without_dates_is_ignored(env):
    sessions, delivered = env
    with sessions() as db:
        add_training(db, start_date=None, end_date=None)
        add_enrolment(db, "a@example.com")
        tn.send_due_training_reminders(db, utc(2026, 10, 4, 4, 0))
    assert delivered == []


# --- 1. enrolment approved: in-app + email ----------------------------------------------

def _staff():
    return {"id": str(uuid4()), "role": "admin", "email": "admin@example.com"}


def test_approval_sends_in_app_and_email(env):
    sessions, delivered = env
    uid = uuid4()
    with sessions() as db:
        add_training(db, status="published")
        e = add_enrolment(db, "a@example.com", status="pending_approval", user_id=uid, name="Asha")
        service.approve_training_enrol_service(db, TID, e.id, "approve", current_user=_staff())

    [n] = delivered
    assert n["category"] == "enrolment_approved"
    assert n["send_email"] is True
    assert n["user_id"] == uid and n["email"] == "a@example.com"
    assert "Asha" in n["message"] and "approved" in n["message"]


def test_approving_an_already_enrolled_learner_does_not_notify_again(env):
    sessions, delivered = env
    with sessions() as db:
        add_training(db)
        e = add_enrolment(db, "a@example.com", status="enrolled")
        service.approve_training_enrol_service(db, TID, e.id, "approve", current_user=_staff())
    assert delivered == []


def test_rejection_notifies_in_app_without_email(env):
    sessions, delivered = env
    with sessions() as db:
        add_training(db)
        e = add_enrolment(db, "a@example.com", status="pending_approval")
        service.approve_training_enrol_service(db, TID, e.id, "reject", reason="Class is full", current_user=_staff())
    [n] = delivered
    assert n["category"] == "enrolment_rejected"
    assert not n.get("send_email")
    assert "Class is full" in n["message"]


def test_self_enrolment_records_user_id_and_confirms(env):
    sessions, delivered = env
    uid = uuid4()
    with sessions() as db:
        add_training(db)
        service.create_training_enrol_service(
            db, TID, {}, current_user={"id": str(uid), "email": "me@example.com", "name": "Me"},
        )
        row = db.query(TrainingEnrolment).filter_by(participant_email="me@example.com").one()
    assert row.user_id == uid
    [n] = delivered
    assert n["category"] == "training_enrolment_confirmation" and n["user_id"] == uid
    assert not n.get("send_email")  # only approval emails, not plain confirmation


def test_requires_approval_enrolment_sends_pending_notice(env):
    sessions, delivered = env
    with sessions() as db:
        add_training(db, requires_approval=True)
        service.create_training_enrol_service(db, TID, {}, current_user={"id": str(uuid4()), "email": "me@example.com"})
    assert delivered[0]["title"].startswith("Enrolment Pending")


def test_admin_enrolling_someone_else_does_not_stamp_the_admins_user_id(env):
    sessions, delivered = env
    with sessions() as db:
        add_training(db)
        service.create_training_enrol_service(
            db, TID, {"participant_email": "learner@example.com"},
            current_user={"id": str(uuid4()), "email": "admin@example.com", "role": "admin"},
        )
        row = db.query(TrainingEnrolment).filter_by(participant_email="learner@example.com").one()
    assert row.user_id is None
    assert delivered[0]["user_id"] is None  # falls back to resolving the learner by email


# --- 3. certificate: in-app + email, once -----------------------------------------------

def test_certificate_notification_on_first_completion_only(env):
    sessions, delivered = env
    uid = uuid4()
    with sessions() as db:
        t = add_training(db)
        add_enrolment(db, "a@example.com", user_id=uid, name="Asha")

        service._apply_lesson_completion(db, TID, "l1", "a@example.com", t=t)
        assert delivered == []  # half way — no certificate yet

        service._apply_lesson_completion(db, TID, "l2", "a@example.com", t=t)
        [n] = delivered
        assert n["category"] == "training_certificate"
        assert n["send_email"] is True
        assert n["user_id"] == uid and "Asha" in n["message"]
        assert n["metadata"]["certificate_url"].startswith(f"/api/v1/trainings/{TID}/certificate.pdf")

        service._apply_lesson_completion(db, TID, "l2", "a@example.com", t=t)
        assert len(delivered) == 1  # saving again must not re-send the certificate


# --- 4. announcements -------------------------------------------------------------------

def _announce(db, **kw):
    data = SimpleNamespace(title=kw.get("title", "Venue change"), message="Room moved to B", channel=kw.get("channel", "in_app"))
    return service.create_training_announcement_service(db, TID, data, {"email": "admin@example.com"})


def test_announcement_notifies_every_enrolled_learner_in_app(env):
    sessions, delivered = env
    with sessions() as db:
        add_training(db)
        add_enrolment(db, "a@example.com")
        add_enrolment(db, "b@example.com", status="attended")
        add_enrolment(db, "c@example.com", status="cancelled")
        entry = _announce(db)
    assert sorted(n["email"] for n in delivered) == ["a@example.com", "b@example.com"]
    for n in delivered:
        assert n["category"] == "training_announcement" and n["title"] == "Venue change"
        assert n["message"] == "Room moved to B" and n["in_app"] is True and not n["send_email"]
        assert n["metadata"]["announcement_id"] == entry["id"]


def test_announcement_channel_email_skips_inbox(env):
    sessions, delivered = env
    with sessions() as db:
        add_training(db)
        add_enrolment(db, "a@example.com")
        _announce(db, channel="email")
    [n] = delivered
    assert n["send_email"] is True and n["in_app"] is False


def test_announcement_channel_both_does_inbox_and_email(env):
    sessions, delivered = env
    with sessions() as db:
        add_training(db)
        add_enrolment(db, "a@example.com")
        _announce(db, channel="both")
    [n] = delivered
    assert n["send_email"] is True and n["in_app"] is True


def test_announcement_without_title_gets_a_default(env):
    sessions, delivered = env
    with sessions() as db:
        add_training(db)
        add_enrolment(db, "a@example.com")
        _announce(db, title=None)
    assert delivered[0]["title"] == "Announcement: Ergonomics 101"


# --- 5. admin answers a question --------------------------------------------------------

def _with_question(db, author="learner@example.com", author_id=None):
    t = add_training(db, discussions=[{"id": "d1", "author": author, "author_id": author_id, "question": "Is lunch included?", "answer": None}])
    return t


def test_staff_answer_notifies_the_question_author(env):
    sessions, delivered = env
    uid = str(uuid4())
    with sessions() as db:
        _with_question(db, author_id=uid)
        service.reply_discussion_service(db, TID, "d1", {"text": "Yes, lunch is provided."}, {"email": "admin@example.com", "role": "admin"})
    [n] = delivered
    assert n["category"] == "training_answer"
    assert n["email"] == "learner@example.com" and n["user_id"] == uid
    assert "Yes, lunch is provided." in n["message"]
    assert n["metadata"]["discussion_id"] == "d1"
    assert not n.get("send_email")


def test_another_learners_reply_does_not_notify(env):
    sessions, delivered = env
    with sessions() as db:
        _with_question(db)
        service.reply_discussion_service(db, TID, "d1", {"text": "I think so"}, {"email": "peer@example.com", "role": "customer"})
    assert delivered == []


def test_author_answering_their_own_question_does_not_notify_themselves(env):
    sessions, delivered = env
    with sessions() as db:
        _with_question(db, author="admin@example.com")
        service.reply_discussion_service(db, TID, "d1", {"text": "Found it"}, {"email": "admin@example.com", "role": "admin"})
    assert delivered == []


def test_anonymous_question_author_is_skipped(env):
    sessions, delivered = env
    with sessions() as db:
        _with_question(db, author="anonymous")
        service.reply_discussion_service(db, TID, "d1", {"text": "Yes"}, {"email": "admin@example.com", "role": "provider"})
    assert delivered == []


# --- 2. new training published ----------------------------------------------------------

def test_first_publish_notifies_users_but_republish_does_not(env, monkeypatch):
    sessions, _ = env
    fanouts = []
    monkeypatch.setattr(tn, "_fan_out_new_training", lambda **kw: fanouts.append(kw))
    admin_id = str(uuid4())
    actor = {"id": admin_id, "role": "admin", "email": "admin@example.com"}
    with sessions() as db:
        add_training(db, status="approved", start_date=datetime(2026, 11, 1, 9, 0))
        service.update_training_status_service(db, TID, "published", actor)
        service.update_training_status_service(db, TID, "unpublished", actor)
        service.update_training_status_service(db, TID, "published", actor)

    [call] = fanouts
    assert call["title"] == "New Training: Ergonomics 101"
    assert "01 Nov 2026" in call["message"]
    assert call["tenant_id"] == TENANT and call["exclude_user_id"] == admin_id


def test_other_status_changes_do_not_notify(env, monkeypatch):
    sessions, _ = env
    fanouts = []
    monkeypatch.setattr(tn, "_fan_out_new_training", lambda **kw: fanouts.append(kw))
    with sessions() as db:
        add_training(db, status="draft")
        service.update_training_status_service(db, TID, "pending_approval", _staff())
    assert fanouts == []


def test_fan_out_reaches_tenant_users_except_the_publisher(monkeypatch):
    u1, u2, publisher = uuid4(), uuid4(), uuid4()
    created = []
    monkeypatch.setattr("app.services.invigorate_auth_client.list_tenant_user_ids", lambda tenant: [u1, publisher, u2])
    monkeypatch.setattr("app.db.database.SessionLocal", lambda: contextlib.nullcontext(MagicMock()))
    monkeypatch.setattr("app.services.notification_service.create_automatic_notification", lambda db, **kw: created.append(kw))

    count = tn._fan_out_new_training(
        training_id="t1", tenant_id=str(TENANT), title="New Training: X", message="m",
        metadata={"training_id": "t1"}, exclude_user_id=str(publisher),
    )
    assert count == 2
    [call] = created
    assert call["user_ids"] == [u1, u2] and call["category"] == "training_new"
    assert call["channels"] == ["in_app", "push"] and call["tenant_id"] == TENANT


def test_fan_out_is_a_noop_without_a_tenant_or_without_resolvable_users(monkeypatch):
    created = []
    monkeypatch.setattr("app.services.invigorate_auth_client.list_tenant_user_ids", lambda tenant: [])
    monkeypatch.setattr("app.db.database.SessionLocal", lambda: contextlib.nullcontext(MagicMock()))
    monkeypatch.setattr("app.services.notification_service.create_automatic_notification", lambda db, **kw: created.append(kw))

    assert tn._fan_out_new_training(training_id="t", tenant_id=None, title="x", message="m", metadata={}, exclude_user_id=None) == 0
    assert tn._fan_out_new_training(training_id="t", tenant_id=str(TENANT), title="x", message="m", metadata={}, exclude_user_id=None) == 0
    assert created == []


# --- delivery: email once, in-app via the platform inbox --------------------------------

@pytest.fixture
def delivery(monkeypatch):
    calls = {"email": [], "inbox": [], "resolved": []}
    monkeypatch.setattr("app.db.database.SessionLocal", lambda: contextlib.nullcontext(MagicMock()))
    monkeypatch.setattr(
        "app.services.notification_triggers._send_email_via_smtp",
        lambda to, title, body: calls["email"].append((to, title, body)) or True,
    )
    monkeypatch.setattr(
        "app.services.notification_service.create_automatic_notification",
        lambda db, **kw: calls["inbox"].append(kw),
    )

    def resolver(db, email):
        calls["resolved"].append(email)
        return calls.get("resolve_to")

    monkeypatch.setattr("app.services.notification_triggers._resolve_user_id_from_email", resolver)
    return calls


def test_delivery_sends_email_once_and_keeps_email_out_of_the_inbox_pipeline(delivery):
    uid = uuid4()
    result = tn.deliver_to_participant(
        tenant_id=str(TENANT), email="a@example.com", user_id=uid, title="T", message="M",
        category="enrolment_approved", send_email=True,
    )
    assert result == {"in_app": True, "email": True}
    assert delivery["email"] == [("a@example.com", "T", "M")]
    [inbox] = delivery["inbox"]
    assert inbox["channels"] == ["in_app", "push"]  # "email" here would send the email a second time
    assert inbox["user_ids"] == [uid] and inbox["tenant_id"] == TENANT
    assert delivery["resolved"] == []  # known user id — no email lookup


def test_metadata_carries_category_and_drops_none_values(delivery):
    # Mobile routes on metadata.category (push data has nothing else), and FCM would turn a
    # None value into the string "None".
    tn.deliver_to_participant(
        tenant_id=None, email="a@example.com", user_id=uuid4(), title="T", message="M",
        category="enrolment_rejected", metadata={"training_id": "t1", "reason": None, "enrolment_id": "e1"},
    )
    assert delivery["inbox"][0]["metadata"] == {"training_id": "t1", "enrolment_id": "e1", "category": "enrolment_rejected"}


def test_fan_out_metadata_carries_category(monkeypatch):
    created = []
    monkeypatch.setattr("app.services.invigorate_auth_client.list_tenant_user_ids", lambda tenant: [uuid4()])
    monkeypatch.setattr("app.db.database.SessionLocal", lambda: contextlib.nullcontext(MagicMock()))
    monkeypatch.setattr("app.services.notification_service.create_automatic_notification", lambda db, **kw: created.append(kw))
    tn._fan_out_new_training(
        training_id="t1", tenant_id=str(TENANT), title="x", message="m",
        metadata={"training_id": "t1"}, exclude_user_id=None,
    )
    assert created[0]["metadata"] == {"training_id": "t1", "category": "training_new"}


def test_delivery_without_send_email_sends_none(delivery):
    tn.deliver_to_participant(tenant_id=None, email="a@example.com", user_id=uuid4(), title="T", message="M", category="c")
    assert delivery["email"] == [] and len(delivery["inbox"]) == 1


def test_delivery_resolves_user_by_email_when_user_id_unknown(delivery):
    uid = uuid4()
    delivery["resolve_to"] = uid
    result = tn.deliver_to_participant(tenant_id=None, email="a@example.com", user_id=None, title="T", message="M", category="c")
    assert result["in_app"] is True and delivery["inbox"][0]["user_ids"] == [uid]


def test_delivery_still_emails_when_no_user_can_be_resolved(delivery):
    delivery["resolve_to"] = None
    result = tn.deliver_to_participant(
        tenant_id=None, email="a@example.com", user_id=None, title="T", message="M", category="c", send_email=True,
    )
    assert result == {"in_app": False, "email": True}
    assert delivery["inbox"] == []


def test_email_only_announcement_skips_the_inbox(delivery):
    result = tn.deliver_to_participant(
        tenant_id=None, email="a@example.com", user_id=uuid4(), title="T", message="M", category="c",
        send_email=True, in_app=False,
    )
    assert result == {"in_app": False, "email": True} and delivery["inbox"] == []


def test_delivery_never_raises_when_the_inbox_pipeline_fails(delivery, monkeypatch):
    def boom(db, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.services.notification_service.create_automatic_notification", boom)
    result = tn.deliver_to_participant(
        tenant_id=None, email="a@example.com", user_id=uuid4(), title="T", message="M", category="c", send_email=True,
    )
    assert result == {"in_app": False, "email": True}


# --- scheduler wiring --------------------------------------------------------------------

def test_scheduler_is_off_by_default_in_development_and_on_in_production(monkeypatch):
    monkeypatch.setattr(settings, "TRAINING_REMINDERS_ENABLED", None)
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    assert settings.training_reminders_enabled is False
    assert tn.start_reminder_scheduler() is None
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    assert settings.training_reminders_enabled is True


def test_scheduler_can_be_forced_on_and_starts_a_daemon_thread(monkeypatch):
    monkeypatch.setattr(settings, "TRAINING_REMINDERS_ENABLED", True)
    started = []
    monkeypatch.setattr(tn, "_scheduler_loop", lambda interval: started.append(interval))
    thread = tn.start_reminder_scheduler()
    thread.join(timeout=2)
    assert thread.daemon is True and started == [settings.TRAINING_REMINDER_INTERVAL_SECONDS]
