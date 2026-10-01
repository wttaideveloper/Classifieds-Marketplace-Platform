"""Reproduces the reported bug: GET /trainings/{id} returns enrolment_start
as a naive "2026-10-01T13:50:00" with time_zone "Asia/Kolkata", meaning
1:50 PM IST — but POST /trainings/{id}/enroll compared that naive value
directly against datetime.utcnow(), treating it as if it were already UTC.
At 15:30 IST (10:00 UTC) that made the window look not-yet-open until
19:20 IST instead of 13:50 IST. _enforce_enrolment_window must interpret
enrolment_start/enrolment_end as wall-clock time in training.time_zone
(mirroring app.utils.event_utils, already proven for Events)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.training_service import _enforce_enrolment_window, create_training_enrol_service


def _training(**overrides):
    defaults = dict(
        id=uuid4(), status="published", coupon_code=None, capacity=None,
        requires_approval=False, access_duration_days=None,
        enrolment_start=None, enrolment_end=None, time_zone="Asia/Kolkata",
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


def _freeze(monkeypatch, utc_dt: datetime):
    """Pins app.utils.event_utils._get_utc_now, which _enforce_enrolment_window
    re-imports fresh on every call — patching the module attribute is enough."""
    monkeypatch.setattr("app.utils.event_utils._get_utc_now", lambda now=None: utc_dt)


# --- the core bug: enrolment_start interpreted in training.time_zone, not UTC ---

def test_enrolment_open_after_local_start_even_though_naive_utc_compare_would_block(monkeypatch):
    # enrolment_start = 13:50 IST. "Now" = 10:00 UTC = 15:30 IST — after open.
    # The old buggy code compared 10:00 UTC (naive) directly against the naive
    # 13:50 and wrongly concluded enrolment was still closed.
    training = _training(enrolment_start=datetime(2026, 10, 1, 13, 50, 0))
    _freeze(monkeypatch, datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc))

    _enforce_enrolment_window(training)  # must not raise


def test_enrolment_not_yet_open_before_local_start(monkeypatch):
    # "Now" = 07:00 UTC = 12:30 IST — before the 13:50 IST open time.
    training = _training(enrolment_start=datetime(2026, 10, 1, 13, 50, 0))
    _freeze(monkeypatch, datetime(2026, 10, 1, 7, 0, 0, tzinfo=timezone.utc))

    with pytest.raises(HTTPException) as exc:
        _enforce_enrolment_window(training)
    assert exc.value.status_code == 400
    assert "not yet open" in exc.value.detail
    assert "2026-10-01T13:50:00+05:30" in exc.value.detail
    assert "(Asia/Kolkata)" in exc.value.detail


def test_enrolment_closed_uses_training_timezone(monkeypatch):
    # enrolment_end = 20:00 IST = 14:30 UTC. "Now" = 15:00 UTC (20:30 IST) — past close.
    training = _training(enrolment_end=datetime(2026, 10, 1, 20, 0, 0))
    _freeze(monkeypatch, datetime(2026, 10, 1, 15, 0, 0, tzinfo=timezone.utc))

    with pytest.raises(HTTPException) as exc:
        _enforce_enrolment_window(training)
    assert exc.value.status_code == 400
    assert "closed" in exc.value.detail
    assert "2026-10-01T20:00:00+05:30" in exc.value.detail
    assert "(Asia/Kolkata)" in exc.value.detail


def test_enrolment_open_before_local_close(monkeypatch):
    training = _training(enrolment_end=datetime(2026, 10, 1, 20, 0, 0))
    _freeze(monkeypatch, datetime(2026, 10, 1, 13, 0, 0, tzinfo=timezone.utc))  # 18:30 IST

    _enforce_enrolment_window(training)  # must not raise


def test_no_window_configured_does_not_raise(monkeypatch):
    training = _training()
    _freeze(monkeypatch, datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc))
    _enforce_enrolment_window(training)


def test_invalid_timezone_falls_back_to_utc_without_crashing(monkeypatch):
    training = _training(enrolment_start=datetime(2030, 1, 1, 0, 0, 0), time_zone="Not/AZone")
    _freeze(monkeypatch, datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc))

    with pytest.raises(HTTPException) as exc:
        _enforce_enrolment_window(training)
    assert "(UTC)" in exc.value.detail


def test_missing_timezone_defaults_to_utc(monkeypatch):
    training = _training(enrolment_start=datetime(2030, 1, 1, 0, 0, 0), time_zone=None)
    _freeze(monkeypatch, datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc))

    with pytest.raises(HTTPException) as exc:
        _enforce_enrolment_window(training)
    assert "(UTC)" in exc.value.detail


# --- end-to-end through the real enroll path ---

def test_enrol_service_succeeds_after_local_open_time(monkeypatch):
    from app.services import training_service

    training = _training(enrolment_start=datetime(2026, 10, 1, 13, 50, 0))
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    _freeze(monkeypatch, datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc))  # 15:30 IST

    db = MagicMock()
    db.query.return_value.filter.return_value.count.return_value = 0

    result = create_training_enrol_service(
        db, training.id, {"participant_name": "Alex", "participant_email": "alex@example.com"},
    )
    assert result is not None


def test_enrol_service_still_blocked_before_local_open_time(monkeypatch):
    from app.services import training_service

    training = _training(enrolment_start=datetime(2026, 10, 1, 13, 50, 0))
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)
    _freeze(monkeypatch, datetime(2026, 10, 1, 7, 0, 0, tzinfo=timezone.utc))  # 12:30 IST

    db = MagicMock()
    db.query.return_value.filter.return_value.count.return_value = 0

    with pytest.raises(HTTPException) as exc:
        create_training_enrol_service(
            db, training.id, {"participant_name": "Alex", "participant_email": "alex@example.com"},
        )
    assert exc.value.status_code == 400
    assert "not yet open" in exc.value.detail
