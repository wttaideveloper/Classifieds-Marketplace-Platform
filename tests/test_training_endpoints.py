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
        instructor_role=None, instructor_photo=None, instructor_credentials=None,
        requirements="none", prerequisites=[], subtitle=None, learning_objectives=["Learn widgets"],
        target_audience=None, level=None, language="English",
        primary_image=None, gallery_images=[], promotional_video=None, documents=[],
        delivery_mode="self_paced", course_type=None, duration=None, duration_hours=None,
        start_date=datetime(2026, 1, 1), end_date=datetime(2026, 1, 2),
        start_time="09:00", end_time="17:00", venue="Main Hall", address="123 Main St",
        meeting_link="https://meet.example.com/x", meeting_provider=None, meeting_id=None, meeting_passcode=None,
        access_information=None,
        delivery_instructions="Bring a laptop",
        moderation_status=None, rejection_reason=None,
        published_at=None, approved_at=None, archived_at=None, suspended_at=None, cancelled_at=None,
        recurring=None, schedule_exceptions=[], instructor_notes=None, session_mode=None,
        check_in=False, pass_code=None, qr_payload=None,
        release_rule=None, scheduled_publication=None, randomise=False, is_mandatory=False,
        faqs=[], badges=[],
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
    db.query.return_value.filter.return_value.all.return_value = []

    result = training_service.get_training_service(db, training.id)

    assert result.enrolled_count == 3
    assert result.available_slots == 7
    assert result.instructor_name == "Jane Doe"
    assert result.venue == "Main Hall"
    assert result.meeting_link == "https://meet.example.com/x"
    assert result.learning_objectives == ["Learn widgets"]


def test_get_training_service_exposes_new_fields_and_waitlist_reviews(monkeypatch):
    """GET /trainings/{id} must expose meeting_provider, access_information,
    recurring, schedule_exceptions, instructor_notes, session_mode, check_in,
    pass_code, qr_payload, notes_pdf_url, instructor object, waitlist_count,
    and an embedded reviews array."""
    from datetime import datetime
    from app.services import training_service

    training = _training_stub(
        capacity="10",
        meeting_provider="zoom",
        access_information="Use code 123 at the front desk",
        recurring={"frequency": "weekly", "interval": 1, "days_of_week": ["mon"], "end_date": None},
        schedule_exceptions=["2026-01-05"],
        instructor_notes="Bring extra slides",
        session_mode="live",
        check_in=True,
        pass_code="123456",
        qr_payload='{"training_id": "x", "pass_code": "123456"}',
    )
    monkeypatch.setattr(training_service, "get_training_by_id", lambda db, tid: training)

    review = MagicMock(
        id=uuid4(), training_id=training.id, participant_email="a@x.com",
        rating="5", comment="Great course", created_at=datetime(2026, 1, 1),
    )

    db = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        name = model.__name__
        if name == "TrainingEnrolment":
            q.filter.return_value.count.return_value = 2
        elif name == "TrainingReview":
            q.filter.return_value.all.return_value = [review]
        elif name == "TrainingWaitlist":
            q.filter.return_value.count.return_value = 4
        return q

    db.query.side_effect = query_side_effect

    result = training_service.get_training_service(db, training.id)

    assert result.meeting_provider == "zoom"
    assert result.access_information == "Use code 123 at the front desk"
    assert result.recurring == {"frequency": "weekly", "interval": 1, "days_of_week": ["mon"], "end_date": None}
    assert result.schedule_exceptions == ["2026-01-05"]
    assert result.instructor_notes == "Bring extra slides"
    assert result.session_mode == "live"
    assert result.check_in is True
    assert result.pass_code == "123456"
    assert result.notes_pdf_url.endswith("/notes.pdf")
    assert result.instructor.name == "Jane Doe"
    assert result.instructor.bio == "10 years experience"
    assert result.waitlist_count == 4
    assert len(result.reviews) == 1
    assert result.reviews[0]["participant_email"] == "a@x.com"


def test_create_training_service_generates_pass_code_and_qr_payload_when_check_in_enabled(monkeypatch):
    """check_in=True on create must auto-generate a pass_code + qr_payload —
    these are server-generated, never client-supplied."""
    import json
    from app.services import training_service
    from app.schemas.training_schema import TrainingCreate

    monkeypatch.setattr(training_service, "_validate", lambda *a, **kw: None)
    monkeypatch.setattr(
        "app.services.training_form_config_service.apply_form_configuration_to_training_data",
        lambda db, data, current_user: {},
    )

    data = TrainingCreate(enterprise_id=uuid4(), title="Widgets 101", category="General", check_in=True)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = MagicMock(tenant_id=uuid4())
    db.refresh.side_effect = lambda obj: setattr(obj, "id", obj.id or uuid4())

    result = training_service.create_training_service(db, data)

    assert result.check_in is True
    assert result.pass_code is not None and len(result.pass_code) == 6
    payload = json.loads(result.qr_payload)
    assert payload["pass_code"] == result.pass_code


def test_create_training_service_no_pass_code_when_check_in_disabled(monkeypatch):
    from app.services import training_service
    from app.schemas.training_schema import TrainingCreate

    monkeypatch.setattr(training_service, "_validate", lambda *a, **kw: None)
    monkeypatch.setattr(
        "app.services.training_form_config_service.apply_form_configuration_to_training_data",
        lambda db, data, current_user: {},
    )

    data = TrainingCreate(enterprise_id=uuid4(), title="Widgets 101", category="General")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = MagicMock(tenant_id=uuid4())
    db.refresh.side_effect = lambda obj: setattr(obj, "id", obj.id or uuid4())

    result = training_service.create_training_service(db, data)

    assert result.check_in is False
    assert result.pass_code is None
    assert result.qr_payload is None


def test_check_in_training_service_success(monkeypatch):
    from app.services import training_service

    training = MagicMock(check_in=True, pass_code="123456")
    enrol = MagicMock(status="enrolled", checked_in_at=None)
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    monkeypatch.setattr(training_service, "_get_enrolment", lambda db, tid, email: enrol)

    result = training_service.check_in_training_service(MagicMock(), uuid4(), "learner@example.com", "123456")

    assert result["participant_email"] == "learner@example.com"
    assert enrol.checked_in_at is not None


def test_check_in_training_service_rejects_wrong_pass_code(monkeypatch):
    from app.services import training_service

    training = MagicMock(check_in=True, pass_code="123456")
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)

    with pytest.raises(HTTPException) as exc:
        training_service.check_in_training_service(MagicMock(), uuid4(), "learner@example.com", "000000")
    assert exc.value.status_code == 403


def test_check_in_training_service_requires_check_in_enabled(monkeypatch):
    from app.services import training_service

    training = MagicMock(check_in=False)
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)

    with pytest.raises(HTTPException) as exc:
        training_service.check_in_training_service(MagicMock(), uuid4(), "learner@example.com", "123456")
    assert exc.value.status_code == 400


def test_check_in_training_service_requires_active_enrolment(monkeypatch):
    from app.services import training_service

    training = MagicMock(check_in=True, pass_code="123456")
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    monkeypatch.setattr(training_service, "_get_enrolment", lambda db, tid, email: None)

    with pytest.raises(HTTPException) as exc:
        training_service.check_in_training_service(MagicMock(), uuid4(), "learner@example.com", "123456")
    assert exc.value.status_code == 403


def test_get_certificate_service_404s_when_not_yet_earned(monkeypatch):
    """A certificate that hasn't been earned must 404, not the old 400 —
    lets the UI distinguish 'not earned yet' from a real client error."""
    from app.services import training_service

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    with pytest.raises(HTTPException) as exc:
        training_service.get_certificate_service(db, uuid4(), "learner@example.com")
    assert exc.value.status_code == 404


def test_generate_certificate_pdf_service_produces_a_real_pdf(monkeypatch):
    """Certificate must be an actual generated PDF, not a placeholder URL
    that points back at this same JSON endpoint."""
    from datetime import datetime
    from app.services import training_service

    training = MagicMock(title="Widgets 101")
    prog = MagicMock(
        certificate_url="/api/v1/trainings/x/certificate.pdf?participant_email=learner@example.com",
        completed_at=datetime(2026, 1, 1),
        overall_percent="100",
    )
    enrol = MagicMock(participant_name="Alex Learner")

    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    monkeypatch.setattr(training_service, "_get_enrolment", lambda db, tid, email: enrol)

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = prog

    pdf_bytes = training_service.generate_certificate_pdf_service(db, uuid4(), "learner@example.com")
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 100


def test_generate_certificate_pdf_service_404s_when_not_earned(monkeypatch):
    from app.services import training_service

    training = MagicMock(title="Widgets 101")
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    with pytest.raises(HTTPException) as exc:
        training_service.generate_certificate_pdf_service(db, uuid4(), "learner@example.com")
    assert exc.value.status_code == 404


def test_training_create_flattens_nested_instructor_object():
    """FE contract confirmation: instructor: {id, name, bio, role} must be
    accepted on create as an alternative to the flat instructor_* fields."""
    from app.schemas.training_schema import TrainingCreate

    instructor_id = uuid4()
    data = TrainingCreate(
        enterprise_id=uuid4(), title="Widgets 101", category="General",
        instructor={"id": str(instructor_id), "name": "Jane Doe", "bio": "10 years", "role": "Lead Instructor"},
    )
    model_data = data.to_model_data()
    assert model_data["instructor_id"] == instructor_id
    assert model_data["instructor_name"] == "Jane Doe"
    assert model_data["instructor_bio"] == "10 years"
    assert model_data["instructor_role"] == "Lead Instructor"


def test_training_create_flat_instructor_fields_win_over_nested_object():
    from app.schemas.training_schema import TrainingCreate

    data = TrainingCreate(
        enterprise_id=uuid4(), title="Widgets 101", category="General",
        instructor_name="Flat Name",
        instructor={"name": "Nested Name"},
    )
    assert data.to_model_data()["instructor_name"] == "Flat Name"


def test_training_update_flattens_nested_instructor_object():
    from app.schemas.training_schema import TrainingUpdate

    data = TrainingUpdate(instructor={"name": "Jane Doe", "bio": "10 years", "role": "Lead Instructor"})
    model_data = data.to_model_data()
    assert model_data["instructor_name"] == "Jane Doe"
    assert model_data["instructor_bio"] == "10 years"
    assert model_data["instructor_role"] == "Lead Instructor"
    assert "instructor" not in model_data


def test_lesson_create_accepts_meeting_link_and_file_size():
    from app.schemas.training_schema import LessonCreate

    lesson = LessonCreate(title="Live Q&A", type="live", meeting_link="https://zoom.us/j/123", file_size="24 MB", is_downloadable=True)
    assert lesson.meeting_link == "https://zoom.us/j/123"
    assert lesson.file_size == "24 MB"
    assert lesson.is_downloadable is True


def test_get_training_service_available_slots_null_when_capacity_not_numeric(monkeypatch):
    from app.services import training_service

    training = _training_stub(capacity="unlimited")
    monkeypatch.setattr(training_service, "get_training_by_id", lambda db, tid: training)

    db = MagicMock()
    db.query.return_value.filter.return_value.count.return_value = 5
    db.query.return_value.filter.return_value.all.return_value = []

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
    db.query.return_value.filter.return_value.all.return_value = []

    result = training_service.get_training_service(db, training.id)

    assert result.available_slots == 0
