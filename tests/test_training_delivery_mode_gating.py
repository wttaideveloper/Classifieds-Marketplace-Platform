"""Delivery-mode field gating (online/physical/hybrid/self_paced) and the
moderation_status/timestamp fields that get set on each status transition."""

from unittest.mock import MagicMock
from uuid import uuid4
from tests.test_training_endpoints import _training_stub

import pytest
from fastapi import HTTPException

from app.services import training_service
from tests.test_training_endpoints import _training_stub


def test_online_without_meeting_link_rejected(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        training_service._validate_delivery_mode_fields("online", venue=None, meeting_link=None)
    assert exc.value.status_code == 400
    assert "meeting_link" in exc.value.detail


def test_online_with_meeting_link_ok():
    training_service._validate_delivery_mode_fields("online", venue=None, meeting_link="https://zoom.us/j/x")  # no raise


def test_physical_without_venue_rejected():
    with pytest.raises(HTTPException) as exc:
        training_service._validate_delivery_mode_fields("physical", venue=None, meeting_link=None)
    assert "venue" in exc.value.detail


def test_physical_with_venue_ok():
    training_service._validate_delivery_mode_fields("physical", venue="Main Hall", meeting_link=None)  # no raise


def test_hybrid_requires_both_venue_and_meeting_link():
    with pytest.raises(HTTPException):
        training_service._validate_delivery_mode_fields("hybrid", venue=None, meeting_link="https://x")
    with pytest.raises(HTTPException):
        training_service._validate_delivery_mode_fields("hybrid", venue="Hall", meeting_link=None)
    training_service._validate_delivery_mode_fields("hybrid", venue="Hall", meeting_link="https://x")  # no raise


def test_self_paced_requires_nothing():
    training_service._validate_delivery_mode_fields("self_paced", venue=None, meeting_link=None)  # no raise


def test_legacy_delivery_mode_values_are_ungated():
    """Existing self_paced|instructor_led|blended trainings must not start
    failing validation just because this feature shipped."""
    training_service._validate_delivery_mode_fields("instructor_led", venue=None, meeting_link=None)
    training_service._validate_delivery_mode_fields("blended", venue=None, meeting_link=None)


def test_access_type_mapping():
    assert training_service._access_type_for_delivery_mode("online") == "online"
    assert training_service._access_type_for_delivery_mode("physical") == "venue"
    assert training_service._access_type_for_delivery_mode("hybrid") == "both"
    assert training_service._access_type_for_delivery_mode("self_paced") == "on_demand"
    assert training_service._access_type_for_delivery_mode("instructor_led") is None


def test_create_training_service_rejects_online_without_meeting_link(monkeypatch):
    from app.schemas.training_schema import TrainingCreate

    monkeypatch.setattr(training_service, "_validate", lambda *a, **kw: None)
    monkeypatch.setattr(
        "app.services.training_form_config_service.apply_form_configuration_to_training_data",
        lambda db, data, current_user: {},
    )
    data = TrainingCreate(enterprise_id=uuid4(), title="Widgets", category="General", delivery_mode="online")
    db = MagicMock()

    with pytest.raises(HTTPException) as exc:
        training_service.create_training_service(db, data)
    assert exc.value.status_code == 400


def test_create_training_service_accepts_online_with_meeting_link(monkeypatch):
    from app.schemas.training_schema import TrainingCreate

    monkeypatch.setattr(training_service, "_validate", lambda *a, **kw: None)
    monkeypatch.setattr(
        "app.services.training_form_config_service.apply_form_configuration_to_training_data",
        lambda db, data, current_user: {},
    )
    data = TrainingCreate(enterprise_id=uuid4(), title="Widgets", category="General", delivery_mode="online", meeting_link="https://zoom.us/j/x")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = MagicMock(tenant_id=uuid4())
    db.refresh.side_effect = lambda obj: setattr(obj, "id", obj.id or uuid4())

    result = training_service.create_training_service(db, data)
    assert result.delivery_mode == "online"


# --- moderation_status / timestamps on status transitions ---

def test_publish_sets_published_at_and_approved_moderation_status(monkeypatch):
    # Legitimate path only: publish is reachable from "approved" (post Super
    # Admin review), never directly from "draft" — see test_training_moderation_transitions.py.
    training = _training_stub(status="approved", is_deleted=False, delivery_mode="self_paced", pass_code=None)
    monkeypatch.setattr(training_service, "get_training_by_id", lambda db, tid, include_deleted=True: training)

    training_service.update_training_status_service(MagicMock(), uuid4(), "published")
    assert training.status == "published"
    assert training.published_at is not None
    assert training.moderation_status == "approved"


def test_publish_physical_training_generates_qr(monkeypatch):
    training = _training_stub(id=uuid4(), status="approved", is_deleted=False, delivery_mode="physical", pass_code=None)
    monkeypatch.setattr(training_service, "get_training_by_id", lambda db, tid, include_deleted=True: training)

    training_service.update_training_status_service(MagicMock(), uuid4(), "published")
    assert training.check_in is True
    assert training.pass_code is not None
    assert training.qr_payload is not None


def test_publish_online_training_does_not_generate_qr(monkeypatch):
    training = _training_stub(id=uuid4(), status="approved", is_deleted=False, delivery_mode="online", pass_code=None, check_in=False)
    monkeypatch.setattr(training_service, "get_training_by_id", lambda db, tid, include_deleted=True: training)

    training_service.update_training_status_service(MagicMock(), uuid4(), "published")
    assert training.pass_code is None


def test_reject_sets_moderation_status_and_reason(monkeypatch):
    training = _training_stub(status="pending_approval", is_deleted=False)
    monkeypatch.setattr(training_service, "get_training_by_id", lambda db, tid, include_deleted=True: training)

    training_service.update_training_status_service(MagicMock(), uuid4(), "rejected", notes="Missing prerequisites")
    assert training.moderation_status == "rejected"
    assert training.rejection_reason == "Missing prerequisites"


def test_archive_sets_archived_at(monkeypatch):
    training = _training_stub(status="completed", is_deleted=False)
    monkeypatch.setattr(training_service, "get_training_by_id", lambda db, tid, include_deleted=True: training)

    training_service.update_training_status_service(MagicMock(), uuid4(), "archived")
    assert training.archived_at is not None
