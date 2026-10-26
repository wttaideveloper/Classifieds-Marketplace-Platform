"""Timed quizzes: the `started_at` the apps send, the late auto-submit allowance and blank answers."""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from tests.test_assessment_attempt_review import configure
from tests.test_training_progress_consistency import setup, TID  # noqa: F401

QUESTIONS = [
    {"id": "q1", "question_text": "Number?", "question_type": "single_choice", "options": [{"id": "a", "label": "1"}, {"id": "b", "label": "2"}], "correct_answer": "1", "points": 1},
    {"id": "q2", "question_text": "True?", "question_type": "true_false", "options": [], "correct_answer": "true", "points": 1},
]


def utc_text(minutes_ago, seconds_ago=0, style="z-millis"):
    moment = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago, seconds=seconds_ago)
    return {
        "z-millis": moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z",
        "z": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "offset": moment.isoformat(),
        "naive": moment.replace(tzinfo=None).isoformat(),
        "ist": moment.astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat(),
    }[style]


def submit(client, answers, started_at):
    body = {"answers": answers}
    if started_at is not None:
        body["started_at"] = started_at
    return client.post(f"/api/v1/trainings/{TID}/assessments/assess-1/submit", json=body)


@pytest.mark.parametrize("style", ["z-millis", "z", "offset", "naive", "ist"])
def test_every_common_iso_format_is_accepted(setup, style):
    sessions, client = setup
    configure(sessions, QUESTIONS, time_limit_minutes=5)

    resp = submit(client, [{"question_id": "q1", "answer": "a"}, {"question_id": "q2", "answer": "true"}], utc_text(1, style=style))

    assert resp.status_code == 201, resp.text


def test_a_blank_auto_submit_a_few_seconds_late_still_counts(setup):
    sessions, client = setup
    configure(sessions, QUESTIONS, time_limit_minutes=5)

    resp = submit(client, [{"question_id": "q1", "answer": ""}, {"question_id": "q2", "answer": ""}], utc_text(5, 8))

    assert resp.status_code == 201 and resp.json()["score"] == 0


def test_an_omitted_answer_is_also_fine(setup):
    sessions, client = setup
    configure(sessions, QUESTIONS, time_limit_minutes=5)

    assert submit(client, [{"question_id": "q1", "answer": "a"}], utc_text(2)).status_code == 201


def test_after_the_allowance_it_is_too_late(setup, monkeypatch):
    sessions, client = setup
    monkeypatch.setattr(settings, "ASSESSMENT_SUBMIT_GRACE_SECONDS", 60)
    configure(sessions, QUESTIONS, time_limit_minutes=5)

    resp = submit(client, [{"question_id": "q1", "answer": "a"}], utc_text(6, 30))

    assert resp.status_code == 400 and "Time limit exceeded" in resp.json()["detail"]


def test_the_allowance_can_be_switched_off(setup, monkeypatch):
    sessions, client = setup
    monkeypatch.setattr(settings, "ASSESSMENT_SUBMIT_GRACE_SECONDS", 0)
    configure(sessions, QUESTIONS, time_limit_minutes=5)

    assert submit(client, [{"question_id": "q1", "answer": "a"}], utc_text(5, 8)).status_code == 400


def test_a_start_time_in_the_future_is_refused(setup):
    sessions, client = setup
    configure(sessions, QUESTIONS, time_limit_minutes=5)

    resp = submit(client, [{"question_id": "q1", "answer": "a"}], utc_text(-30))

    assert resp.status_code == 400 and "future" in resp.json()["detail"]


@pytest.mark.parametrize("bad", ["yesterday", "26/10/2026 07:22", "2026-13-45T00:00:00Z"])
def test_text_that_is_not_a_time_is_refused_with_an_example(setup, bad):
    sessions, client = setup
    configure(sessions, QUESTIONS, time_limit_minutes=5)

    resp = submit(client, [{"question_id": "q1", "answer": "a"}], bad)

    assert resp.status_code == 400 and "Invalid started_at" in resp.json()["detail"] and "2026-10-26T07:22:00Z" in resp.json()["detail"]


def test_started_at_is_still_required_for_a_timed_quiz(setup):
    sessions, client = setup
    configure(sessions, QUESTIONS, time_limit_minutes=5)

    resp = submit(client, [{"question_id": "q1", "answer": "a"}], None)

    assert resp.status_code == 400 and "required" in resp.json()["detail"]


def test_a_refused_submit_does_not_use_up_an_attempt(setup):
    sessions, client = setup
    configure(sessions, QUESTIONS, time_limit_minutes=5)
    answers = [{"question_id": "q1", "answer": "a"}]

    assert submit(client, answers, utc_text(20)).status_code == 400
    assert submit(client, answers, utc_text(1)).status_code == 201
    assert client.get(f"/api/v1/trainings/{TID}/assignments/assess-1/attempts").json()[0]["score"] == 1
