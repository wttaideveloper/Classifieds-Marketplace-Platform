"""Per-enrolment QR check-in system for Training — mirrors the existing
Event registration QR check-in (events.service.ts: validateQr / checkIn /
checkOut / batchCheckIn), adapted to Training's enrolment model and
status vocabulary (enrolled/active/approved -> attended, not confirmed)."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services import training_service


def _enrol(**overrides):
    defaults = dict(
        id=uuid4(), training_id=uuid4(), participant_name="Alex Learner",
        participant_email="alex@example.com", status="enrolled", qr_code="ABCD1234EFGH",
        checked_in_at=None, checked_out_at=None, checked_in_by=None,
        access_expires_at=None,
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


# --- qr_code generation on enrolment ---

def test_create_training_enrol_service_generates_qr_code(monkeypatch):
    training = MagicMock(id=uuid4(), status="published", coupon_code=None, capacity=None, requires_approval=False, access_duration_days=None, enrolment_start=None, enrolment_end=None)
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    monkeypatch.setattr(training_service, "_enforce_enrolment_window", lambda t: None)

    created = {}

    def _fake_add(obj):
        created["obj"] = obj

    db = MagicMock()
    db.add.side_effect = _fake_add
    db.query.return_value.filter.return_value.count.return_value = 0

    result = training_service.create_training_enrol_service(db, training.id, {"participant_name": "Alex", "participant_email": "alex@example.com"})
    assert created["obj"].qr_code is not None
    assert len(created["obj"].qr_code) == 12


# --- validate-qr ---

def test_validate_qr_returns_valid_payload_for_matching_code(monkeypatch):
    training = MagicMock(title="Widgets 101")
    enrol = _enrol()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = enrol

    result = training_service.validate_training_qr_service(db, uuid4(), enrol.qr_code)
    assert result["valid"] is True
    assert result["enrolment_id"] == enrol.id
    assert result["training_title"] == "Widgets 101"


def test_validate_qr_404s_for_unknown_code(monkeypatch):
    training = MagicMock(title="Widgets 101")
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    with pytest.raises(HTTPException) as exc:
        training_service.validate_training_qr_service(db, uuid4(), "UNKNOWNCODE")
    assert exc.value.status_code == 404


def test_validate_qr_410s_for_revoked_cancelled_enrolment(monkeypatch):
    training = MagicMock(title="Widgets 101")
    enrol = _enrol(status="cancelled")
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = enrol

    with pytest.raises(HTTPException) as exc:
        training_service.validate_training_qr_service(db, uuid4(), enrol.qr_code)
    assert exc.value.status_code == 410


def test_validate_qr_410s_for_expired_access(monkeypatch):
    training = MagicMock(title="Widgets 101")
    enrol = _enrol(access_expires_at=datetime.utcnow() - timedelta(days=1))
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = enrol

    with pytest.raises(HTTPException) as exc:
        training_service.validate_training_qr_service(db, uuid4(), enrol.qr_code)
    assert exc.value.status_code == 410


# --- check-in ---

def test_check_in_enrolment_flips_enrolled_to_attended(monkeypatch):
    training = MagicMock(status="published")
    enrol = _enrol(status="enrolled")
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    monkeypatch.setattr(training_service, "_find_enrolment_by_id_or_qr", lambda db, tid, eid, qr: enrol)

    result = training_service.check_in_enrolment_service(MagicMock(), uuid4(), enrol.id, None, {"id": "admin-1"})
    assert enrol.status == "attended"
    assert enrol.checked_in_at is not None
    assert enrol.checked_in_by == "admin-1"
    assert result["message"] == "Checked in successfully"


def test_check_in_enrolment_by_qr_code(monkeypatch):
    training = MagicMock(status="published")
    enrol = _enrol(status="active")
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    monkeypatch.setattr(training_service, "_find_enrolment_by_id_or_qr", lambda db, tid, eid, qr: enrol)

    result = training_service.check_in_enrolment_service(MagicMock(), uuid4(), None, enrol.qr_code, {"id": "admin-1"})
    assert enrol.status == "attended"


def test_check_in_enrolment_idempotent_on_rescan(monkeypatch):
    """Re-scanning an already-attended enrolment must not error — idempotent."""
    training = MagicMock(status="published")
    enrol = _enrol(status="attended", checked_in_at=datetime(2026, 1, 1))
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    monkeypatch.setattr(training_service, "_find_enrolment_by_id_or_qr", lambda db, tid, eid, qr: enrol)

    result = training_service.check_in_enrolment_service(MagicMock(), uuid4(), enrol.id, None, {"id": "admin-1"})
    assert result["message"] == "Already checked in"
    assert enrol.status == "attended"


def test_check_in_enrolment_rejects_cancelled(monkeypatch):
    training = MagicMock(status="published")
    enrol = _enrol(status="cancelled")
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    monkeypatch.setattr(training_service, "_find_enrolment_by_id_or_qr", lambda db, tid, eid, qr: enrol)

    with pytest.raises(HTTPException) as exc:
        training_service.check_in_enrolment_service(MagicMock(), uuid4(), enrol.id, None, {"id": "admin-1"})
    assert exc.value.status_code == 400


def test_check_in_enrolment_404s_when_not_found(monkeypatch):
    training = MagicMock(status="published")
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    monkeypatch.setattr(training_service, "_find_enrolment_by_id_or_qr", lambda db, tid, eid, qr: None)

    with pytest.raises(HTTPException) as exc:
        training_service.check_in_enrolment_service(MagicMock(), uuid4(), uuid4(), None, {"id": "admin-1"})
    assert exc.value.status_code == 404


def test_check_in_blocked_when_training_cancelled(monkeypatch):
    training = MagicMock(status="cancelled")
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)

    with pytest.raises(HTTPException) as exc:
        training_service.check_in_enrolment_service(MagicMock(), uuid4(), uuid4(), None, {"id": "admin-1"})
    assert exc.value.status_code == 400


# --- uncheck-in ---

def test_uncheck_in_reverses_attended_to_enrolled(monkeypatch):
    enrol = _enrol(status="attended", checked_in_at=datetime(2026, 1, 1), checked_in_by="admin-1")
    monkeypatch.setattr(training_service, "_find_enrolment_by_id_or_qr", lambda db, tid, eid, qr: enrol)

    result = training_service.uncheck_in_enrolment_service(MagicMock(), uuid4(), enrol.id, None)
    assert enrol.status == "enrolled"
    assert enrol.checked_in_at is None
    assert enrol.checked_in_by is None
    assert result["restored_to"] == "enrolled"


def test_uncheck_in_rejects_non_attended(monkeypatch):
    enrol = _enrol(status="enrolled")
    monkeypatch.setattr(training_service, "_find_enrolment_by_id_or_qr", lambda db, tid, eid, qr: enrol)

    with pytest.raises(HTTPException) as exc:
        training_service.uncheck_in_enrolment_service(MagicMock(), uuid4(), enrol.id, None)
    assert exc.value.status_code == 400


# --- batch check-in preview + batch check-in ---

def test_checkin_preview_marks_eligible_and_ineligible_rows(monkeypatch):
    training = MagicMock()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)

    rows = [
        _enrol(status="enrolled"),
        _enrol(status="attended"),
        _enrol(status="cancelled"),
    ]
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = rows

    result = training_service.list_training_checkin_preview_service(db, uuid4())
    assert result[0]["can_check_in"] is True
    assert result[1]["can_check_in"] is False
    assert result[1]["eligibility_reason"] == "Already checked in"
    assert result[2]["can_check_in"] is False


def test_batch_check_in_reports_succeeded_and_failed(monkeypatch):
    training = MagicMock(status="published")
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)

    good = _enrol(status="enrolled")
    cancelled = _enrol(status="cancelled")

    def _find(db, tid, eid, qr):
        return good if eid == good.id else (cancelled if eid == cancelled.id else None)

    monkeypatch.setattr(training_service, "_find_enrolment_by_id_or_qr", _find)

    participants = [
        {"enrolment_id": good.id},
        {"enrolment_id": cancelled.id},
        {"enrolment_id": uuid4()},
    ]
    result = training_service.batch_check_in_training_enrolments_service(MagicMock(), uuid4(), participants, {"id": "admin-1"})
    assert result["total"] == 3
    assert result["succeeded"] == 1
    assert result["failed"] == 2
    assert good.status == "attended"
