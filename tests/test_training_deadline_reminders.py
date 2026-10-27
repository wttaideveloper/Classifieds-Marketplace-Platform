"""Deadline reminders: "Hurry up! Complete your training before the deadline", by email and as in-app + push together."""
from datetime import datetime, timedelta

import pytest

from app.core.config import settings
from app.models.training_model import TrainingNotificationLog, TrainingProgress
from app.services import training_notifications as tn
from tests.test_training_notifications import TID, add_enrolment, add_training, env, utc  # noqa: F401

# end date Oct 7 2026 17:00 IST. 09:30 IST on Oct 4 is 3 days before; 04:00 UTC = 09:30 IST.
THREE_DAYS_BEFORE = utc(2026, 10, 4, 4, 0)


@pytest.fixture(autouse=True)
def days(monkeypatch):
    monkeypatch.setattr(settings, "TRAINING_DEADLINE_REMINDER_DAYS", "7,3,1")


def run(sessions, now, *, training=None, learners=(("a@example.com", "Asha"),), progress=None):
    with sessions() as db:
        add_training(db, **(training or {}))
        for email, name in learners:
            add_enrolment(db, email, name=name)
        for email, values in (progress or {}).items():
            db.add(TrainingProgress(training_id=TID, participant_email=email, **values))
        db.commit()
        return tn.send_deadline_reminders(db, now)


def test_an_unfinished_learner_gets_email_and_push_in_one_delivery(env):
    sessions, delivered = env

    result = run(sessions, THREE_DAYS_BEFORE)

    assert result == {"sent": 1}
    [n] = delivered
    assert n["category"] == "training_deadline_reminder" and n["title"] == "Hurry up! Complete your training before the deadline"
    assert n["send_email"] is True and n["in_app"] is True                       # email and in-app + push together
    assert "Ergonomics 101" in n["message"] and "in 3 days" in n["message"] and "07 Oct 2026" in n["message"] and "Asha" in n["message"]
    assert n["metadata"]["days_left"] == 3 and n["metadata"]["kind"] == "deadline_3d" and n["email"] == "a@example.com"


def test_progress_so_far_is_mentioned(env):
    sessions, delivered = env

    run(sessions, THREE_DAYS_BEFORE, progress={"a@example.com": {"overall_percent": "40"}})

    assert "completed 40% so far" in delivered[0]["message"]


def test_someone_who_finished_is_not_nagged(env):
    sessions, delivered = env

    result = run(sessions, THREE_DAYS_BEFORE, learners=(("done@example.com", "Dee"), ("todo@example.com", "Tom")),
                 progress={"done@example.com": {"overall_percent": "100", "completed_at": datetime(2026, 10, 3, 12, 0)}})

    assert result == {"sent": 1} and [n["email"] for n in delivered] == ["todo@example.com"]


def test_each_step_goes_out_once(env):
    sessions, delivered = env
    with sessions() as db:
        add_training(db)
        add_enrolment(db, "a@example.com")
        first = tn.send_deadline_reminders(db, THREE_DAYS_BEFORE)
        again = tn.send_deadline_reminders(db, THREE_DAYS_BEFORE + timedelta(hours=3))
        next_day = tn.send_deadline_reminders(db, THREE_DAYS_BEFORE + timedelta(days=1))                 # 2 days left: not a step
        day_before = tn.send_deadline_reminders(db, THREE_DAYS_BEFORE + timedelta(days=2))               # 1 day left
        kinds = sorted(row.kind for row in db.query(TrainingNotificationLog).all())

    assert (first, again, next_day, day_before) == ({"sent": 1}, {"sent": 0}, {"sent": 0}, {"sent": 1})
    assert kinds == ["deadline_1d", "deadline_3d"] and [n["metadata"]["days_left"] for n in delivered] == [3, 1]


def test_nothing_goes_out_before_9am_in_the_training_time_zone(env):
    sessions, delivered = env

    assert run(sessions, utc(2026, 10, 4, 3, 0)) == {"sent": 0} and delivered == []                       # 08:30 IST


def test_the_end_date_itself_is_left_to_the_final_day_notice(env):
    sessions, delivered = env

    assert run(sessions, utc(2026, 10, 7, 4, 0)) == {"sent": 0} and delivered == []


def test_the_end_of_a_learners_access_is_their_deadline_and_includes_the_last_day(env):
    sessions, delivered = env
    with sessions() as db:
        add_training(db, end_date=None, delivery_mode="self_paced")
        enrolment = add_enrolment(db, "a@example.com")
        enrolment.access_expires_at = datetime(2026, 10, 10, 8, 0)                                         # 13:30 IST on Oct 10
        db.commit()
        three = tn.send_deadline_reminders(db, utc(2026, 10, 7, 4, 0))
        last_day = tn.send_deadline_reminders(db, utc(2026, 10, 10, 4, 0))

    assert three == {"sent": 1} and last_day == {"sent": 1}
    assert delivered[0]["metadata"]["deadline_source"] == "access" and "Your access to Ergonomics 101 ends in 3 days" in delivered[0]["message"]
    assert "ends today" in delivered[1]["message"]


def test_the_earlier_of_the_two_deadlines_is_used(env):
    sessions, delivered = env
    with sessions() as db:
        add_training(db)                                                                                    # ends Oct 7
        enrolment = add_enrolment(db, "a@example.com")
        enrolment.access_expires_at = datetime(2026, 10, 5, 8, 0)                                          # access ends Oct 5, 1 day after Oct 4
        db.commit()
        result = tn.send_deadline_reminders(db, THREE_DAYS_BEFORE)

    assert result == {"sent": 1} and delivered[0]["metadata"]["days_left"] == 1 and delivered[0]["metadata"]["deadline_source"] == "access"


def test_a_training_with_no_lessons_has_nothing_to_finish(env):
    sessions, delivered = env

    assert run(sessions, THREE_DAYS_BEFORE, training={"sections": []}) == {"sent": 0} and delivered == []


def test_only_people_who_are_in_the_training_and_only_published_trainings(env):
    sessions, delivered = env
    with sessions() as db:
        add_training(db)
        add_enrolment(db, "waiting@example.com", status="pending_approval")
        add_enrolment(db, "rejected@example.com", status="rejected")
        add_enrolment(db, "in@example.com")
        assert tn.send_deadline_reminders(db, THREE_DAYS_BEFORE) == {"sent": 1}
    assert [n["email"] for n in delivered] == ["in@example.com"]


def test_a_draft_training_sends_nothing(env):
    sessions, delivered = env

    assert run(sessions, THREE_DAYS_BEFORE, training={"status": "draft"}) == {"sent": 0}


def test_the_reminders_can_be_switched_off_or_changed(env, monkeypatch):
    sessions, delivered = env
    monkeypatch.setattr(settings, "TRAINING_DEADLINE_REMINDER_DAYS", "")
    assert run(sessions, THREE_DAYS_BEFORE) == {"sent": 0}
    assert tn.deadline_reminder_days() == []
    monkeypatch.setattr(settings, "TRAINING_DEADLINE_REMINDER_DAYS", " 5, 2 ,x,0,2")
    assert tn.deadline_reminder_days() == [5, 2]
