"""Reproduces the reported bug: enrolling in a training that has ANY
coupon_code configured (a promo/discount code) 400'd with "Invalid coupon
code" even when the enrollee never supplied one — a promo code must be
optional to redeem, not mandatory to enrol."""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.training_service import create_training_checkout_service, create_training_enrol_service


def _training(**overrides):
    defaults = dict(
        id=uuid4(), status="published", enrolment_start=None, enrolment_end=None,
        coupon_code="SUMMER10", capacity=None, requires_approval=False,
        access_duration_days=None, price="100", promo_price="80", currency="INR",
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


def test_enrol_without_coupon_succeeds_even_when_training_has_a_coupon_code(monkeypatch):
    from app.services import training_service

    training = _training()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)

    db = MagicMock()
    db.query.return_value.filter.return_value.count.return_value = 0

    result = create_training_enrol_service(
        db, training.id, {"participant_name": "Alex", "participant_email": "alex@example.com"}, coupon_code=None,
    )
    assert result is not None  # did not raise


def test_enrol_with_wrong_coupon_still_rejected(monkeypatch):
    from app.services import training_service

    training = _training()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)

    db = MagicMock()
    db.query.return_value.filter.return_value.count.return_value = 0

    with pytest.raises(HTTPException) as exc:
        create_training_enrol_service(
            db, training.id, {"participant_name": "Alex", "participant_email": "alex@example.com"}, coupon_code="WRONGCODE",
        )
    assert exc.value.status_code == 400
    assert "Invalid coupon code" in exc.value.detail


def test_enrol_with_correct_coupon_succeeds(monkeypatch):
    from app.services import training_service

    training = _training()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)

    db = MagicMock()
    db.query.return_value.filter.return_value.count.return_value = 0

    result = create_training_enrol_service(
        db, training.id, {"participant_name": "Alex", "participant_email": "alex@example.com"}, coupon_code="SUMMER10",
    )
    assert result is not None


def test_enrol_falls_back_to_authenticated_user_email_when_payload_omits_it(monkeypatch):
    """Reproduces the reported bug: POST /enroll succeeded but the row was
    stored under the literal placeholder 'user@example.com' (since the
    client's body carried no participant_email), so GET /my/enrolments —
    which filters by the authenticated user's real email — always came back
    empty. participant_email must fall back to current_user's email."""
    from app.services import training_service

    training = _training()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)

    db = MagicMock()
    db.query.return_value.filter.return_value.count.return_value = 0

    created = {}
    db.add.side_effect = lambda obj: created.setdefault("obj", obj)

    create_training_enrol_service(
        db, training.id, {}, current_user={"email": "real.user@example.com", "name": "Real User"},
    )
    assert created["obj"].participant_email == "real.user@example.com"
    assert created["obj"].participant_name == "Real User"


def test_enrol_raises_400_when_no_email_available_anywhere(monkeypatch):
    """No more silent 'user@example.com' placeholder — if neither the
    request body nor the authenticated session carries an email, fail loudly
    instead of persisting an unfindable enrolment."""
    from app.services import training_service

    training = _training()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)

    db = MagicMock()
    db.query.return_value.filter.return_value.count.return_value = 0

    with pytest.raises(HTTPException) as exc:
        create_training_enrol_service(db, training.id, {}, current_user=None)
    assert exc.value.status_code == 400
    assert "participant_email" in exc.value.detail


def test_enrol_payload_email_wins_over_authenticated_user_email(monkeypatch):
    """An explicit participant_email in the body (e.g. admin enrolling
    someone else) must not be silently overridden by the caller's own
    session email."""
    from app.services import training_service

    training = _training()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)

    db = MagicMock()
    db.query.return_value.filter.return_value.count.return_value = 0

    created = {}
    db.add.side_effect = lambda obj: created.setdefault("obj", obj)

    create_training_enrol_service(
        db, training.id, {"participant_email": "someone.else@example.com"},
        current_user={"email": "admin@example.com"},
    )
    assert created["obj"].participant_email == "someone.else@example.com"


def test_checkout_without_coupon_succeeds_even_when_training_has_a_coupon_code(monkeypatch):
    from app.services import training_service

    training = _training()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)

    db = MagicMock()
    payload = {"participant_name": "Alex", "participant_email": "alex@example.com", "quantity": 1}

    result = create_training_checkout_service(db, training.id, payload)
    assert result is not None


def test_checkout_with_wrong_coupon_still_rejected(monkeypatch):
    from app.services import training_service

    training = _training()
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda db, tid: training)

    db = MagicMock()
    payload = {"participant_name": "Alex", "participant_email": "alex@example.com", "quantity": 1, "coupon_code": "WRONGCODE"}

    with pytest.raises(HTTPException) as exc:
        create_training_checkout_service(db, training.id, payload)
    assert exc.value.status_code == 400
