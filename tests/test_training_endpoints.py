from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.training_service import (
    ACTIVE_ENROLMENT_STATUSES,
    _is_active_enrolment,
    list_training_assignments_service,
    delete_training_assignment_service,
)


def test_active_enrolment_statuses_include_enrolled_and_approved():
    assert "enrolled" in ACTIVE_ENROLMENT_STATUSES
    assert "approved" in ACTIVE_ENROLMENT_STATUSES


def test_is_active_enrolment():
    assert _is_active_enrolment(MagicMock(status="enrolled")) is True
    assert _is_active_enrolment(MagicMock(status="pending_approval")) is False


def test_list_training_assignments():
    training = MagicMock()
    training.assignments = [{"id": "a1", "title": "Task 1"}]

    from app.services import training_service

    original = training_service._get_training_or_404
    training_service._get_training_or_404 = lambda db, tid: training
    try:
        result = list_training_assignments_service(MagicMock(), uuid4())
        assert result[0]["title"] == "Task 1"
    finally:
        training_service._get_training_or_404 = original


def test_delete_training_assignment_not_found():
    training = MagicMock()
    training.assignments = [{"id": "a1", "title": "Task 1"}]

    from app.services import training_service

    original = training_service._get_training_or_404
    training_service._get_training_or_404 = lambda db, tid: training
    try:
        with pytest.raises(HTTPException) as exc:
            delete_training_assignment_service(MagicMock(), uuid4(), "missing")
        assert exc.value.status_code == 404
    finally:
        training_service._get_training_or_404 = original


def _training_stub(**overrides):
    """A MagicMock standing in for a Training ORM row, with real scalar
    values for every field TrainingDetailResponse requires."""
    from datetime import datetime

    stub = MagicMock()
    defaults = dict(
        id=uuid4(), tenant_id=uuid4(), enterprise_id=uuid4(), location_id=None,
        title="Intro to Widgets", description="desc", category="General", subcategory=None,
        tags=["a"], instructor_id=None, instructor_name="Jane Doe", instructor_bio="10 years experience",
        requirements="none", learning_objectives=["Learn widgets"],
        primary_image=None, gallery_images=[], promotional_video=None, documents=[],
        delivery_mode="self_paced", course_type=None, duration=None,
        start_date=datetime(2026, 1, 1), end_date=datetime(2026, 1, 2),
        start_time="09:00", end_time="17:00", venue="Main Hall", address="123 Main St",
        meeting_link="https://meet.example.com/x", delivery_instructions="Bring a laptop",
        enrolment_start=None, enrolment_end=None, time_zone="UTC",
        capacity="10", price="0", currency="USD", promo_price=None, coupon_code=None,
        status="published", is_deleted=False, created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 1, 1),
        sections=[], assessments=[], assignments=[],
        requires_approval=False, access_duration_days=None, last_admin_notes=None,
        custom_values=[], form_configuration_id=None, form_configuration_version_id=None,
        enterprise=None,
    )
    defaults.update(overrides)
    for key, value in defaults.items():
        setattr(stub, key, value)
    return stub


def test_get_training_service_computes_enrolled_count_and_available_slots(monkeypatch):
    """GET /trainings/{id} must report enrolled_count (active enrolments)
    and available_slots (capacity - enrolled_count)."""
    from app.services import training_service

    training = _training_stub(capacity="10")
    monkeypatch.setattr(training_service, "get_training_by_id", lambda db, tid: training)

    db = MagicMock()
    db.query.return_value.filter.return_value.count.return_value = 3

    result = training_service.get_training_service(db, training.id)

    assert result.enrolled_count == 3
    assert result.available_slots == 7
    assert result.instructor_name == "Jane Doe"
    assert result.venue == "Main Hall"
    assert result.meeting_link == "https://meet.example.com/x"
    assert result.learning_objectives == ["Learn widgets"]


def test_get_training_service_available_slots_null_when_capacity_not_numeric(monkeypatch):
    from app.services import training_service

    training = _training_stub(capacity="unlimited")
    monkeypatch.setattr(training_service, "get_training_by_id", lambda db, tid: training)

    db = MagicMock()
    db.query.return_value.filter.return_value.count.return_value = 5

    result = training_service.get_training_service(db, training.id)

    assert result.enrolled_count == 5
    assert result.available_slots is None


def test_get_training_service_available_slots_never_negative(monkeypatch):
    """More active enrolments than capacity (e.g. capacity lowered after the
    fact) must clamp to 0, not go negative."""
    from app.services import training_service

    training = _training_stub(capacity="2")
    monkeypatch.setattr(training_service, "get_training_by_id", lambda db, tid: training)

    db = MagicMock()
    db.query.return_value.filter.return_value.count.return_value = 5

    result = training_service.get_training_service(db, training.id)

    assert result.available_slots == 0
