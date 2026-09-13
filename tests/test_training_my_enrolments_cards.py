"""GET /trainings/my/enrolments — card fields for the mobile dashboard:
title, primary_image, enterprise_name, delivery_mode, price, currency,
duration, progress_percent, completed_lessons, total_lessons."""

from datetime import datetime
from unittest.mock import MagicMock
from uuid import uuid4

from app.services.training_service import list_my_enrolments_service


def _enrol_row(training_id, **overrides):
    defaults = dict(
        id=uuid4(), training_id=training_id, status="enrolled", qr_code="ABCD1234EFGH",
        created_at=datetime(2026, 1, 1),
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


def _training_row(tid, **overrides):
    enterprise = MagicMock(business_short_name="Pulse Labs")
    defaults = dict(
        id=tid, title="Yoga for Stress Relief", primary_image="https://img/x.jpg",
        enterprise=enterprise, delivery_mode="blended", price="999", currency="INR",
        duration="8 weeks",
        sections=[{"id": "s1", "lessons": [{"id": "l1"}, {"id": "l2"}, {"id": "l3"}, {"id": "l4"}]}],
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


def test_returns_card_fields_from_training_and_progress(monkeypatch):
    tid = uuid4()
    row = _enrol_row(tid)
    training = _training_row(tid)
    prog = MagicMock(training_id=tid, lessons_completed=["l1", "l2"])

    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [row]

    def query_side_effect(model):
        q = MagicMock()
        name = model.__name__
        if name == "TrainingEnrolment":
            q.filter.return_value.order_by.return_value.all.return_value = [row]
        elif name == "Training":
            q.options.return_value.filter.return_value.all.return_value = [training]
        elif name == "TrainingProgress":
            q.filter.return_value.all.return_value = [prog]
        return q

    db.query.side_effect = query_side_effect

    result = list_my_enrolments_service(db, "alex@example.com")

    assert len(result) == 1
    r = result[0]
    assert r["title"] == "Yoga for Stress Relief"
    assert r["primary_image"] == "https://img/x.jpg"
    assert r["enterprise_name"] == "Pulse Labs"
    assert r["delivery_mode"] == "blended"
    assert r["price"] == "999"
    assert r["currency"] == "INR"
    assert r["duration"] == "8 weeks"
    assert r["completed_lessons"] == 2
    assert r["total_lessons"] == 4
    assert r["progress_percent"] == 50.0
    assert r["qr_code"] == "ABCD1234EFGH"


def test_zero_progress_when_no_progress_row(monkeypatch):
    tid = uuid4()
    row = _enrol_row(tid)
    training = _training_row(tid)

    db = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        name = model.__name__
        if name == "TrainingEnrolment":
            q.filter.return_value.order_by.return_value.all.return_value = [row]
        elif name == "Training":
            q.options.return_value.filter.return_value.all.return_value = [training]
        elif name == "TrainingProgress":
            q.filter.return_value.all.return_value = []
        return q

    db.query.side_effect = query_side_effect

    result = list_my_enrolments_service(db, "alex@example.com")
    assert result[0]["completed_lessons"] == 0
    assert result[0]["progress_percent"] == 0


def test_no_enrolments_returns_empty_list_without_querying_trainings():
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

    result = list_my_enrolments_service(db, "alex@example.com")
    assert result == []
