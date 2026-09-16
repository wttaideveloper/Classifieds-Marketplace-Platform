"""Moderation lifecycle enforcement for Training/Course status transitions.

Regression coverage for the fix that removed the "draft" -> "published"
shortcut from update_training_status_service's VALID transition map. The
intended workflow is:

    draft -> pending_approval -> approved -> published

An Enterprise Admin/provider must not be able to publish a draft directly
without it first passing through Super Admin approval (pending_approval ->
approved). Publishing itself (approved -> published) remains available to
admin/provider/super_admin, per the existing /publish and /status routes —
this file does not change or test route-level role gating, only the
underlying state machine those routes all funnel through.
"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services import training_service
from tests.test_training_endpoints import _training_stub


def _patch_training(monkeypatch, training):
    monkeypatch.setattr(training_service, "get_training_by_id", lambda db, tid, include_deleted=True: training)


# --- TEST 1: draft -> published must be rejected ---

def test_draft_to_published_is_rejected(monkeypatch):
    training = _training_stub(status="draft", is_deleted=False)
    _patch_training(monkeypatch, training)

    with pytest.raises(HTTPException) as exc:
        training_service.update_training_status_service(MagicMock(), uuid4(), "published")

    assert exc.value.status_code == 400
    assert "draft" in exc.value.detail
    assert "published" not in exc.value.detail.split("Allowed:")[1]
    # training must remain untouched
    assert training.status == "draft"


def test_draft_to_published_is_rejected_even_for_provider_caller(monkeypatch):
    """The state machine is the enforcement point, independent of which role
    is calling it — this must hold even if a route ever forgets its own
    role check."""
    training = _training_stub(status="draft", is_deleted=False)
    _patch_training(monkeypatch, training)
    provider_user = {"id": str(uuid4()), "role": "provider", "tenant_id": None}

    with pytest.raises(HTTPException) as exc:
        training_service.update_training_status_service(MagicMock(), uuid4(), "published", provider_user)

    assert exc.value.status_code == 400
    assert training.status == "draft"


# --- TEST 2: draft -> pending_approval must succeed ---

def test_draft_to_pending_approval_succeeds(monkeypatch):
    training = _training_stub(status="draft", is_deleted=False)
    _patch_training(monkeypatch, training)

    training_service.update_training_status_service(MagicMock(), uuid4(), "pending_approval")

    assert training.status == "pending_approval"
    assert training.moderation_status == "pending"


# --- TEST 3: pending_approval -> approved must succeed (Super Admin) ---

def test_pending_approval_to_approved_succeeds(monkeypatch):
    training = _training_stub(status="pending_approval", is_deleted=False)
    _patch_training(monkeypatch, training)
    super_admin = {"id": str(uuid4()), "role": "super_admin"}

    training_service.update_training_status_service(MagicMock(), uuid4(), "approved", super_admin)

    assert training.status == "approved"
    assert training.moderation_status == "approved"
    assert training.approved_at is not None


# --- TEST 4: approved -> published must succeed for the authorized role ---

def test_approved_to_published_succeeds(monkeypatch):
    training = _training_stub(status="approved", is_deleted=False, delivery_mode="self_paced", pass_code=None)
    _patch_training(monkeypatch, training)
    provider_user = {"id": str(uuid4()), "role": "provider", "tenant_id": None}

    training_service.update_training_status_service(MagicMock(), uuid4(), "published", provider_user)

    assert training.status == "published"
    assert training.published_at is not None
    assert training.moderation_status == "approved"


def test_approved_to_published_succeeds_for_super_admin(monkeypatch):
    training = _training_stub(status="approved", is_deleted=False, delivery_mode="self_paced", pass_code=None)
    _patch_training(monkeypatch, training)
    super_admin = {"id": str(uuid4()), "role": "super_admin"}

    training_service.update_training_status_service(MagicMock(), uuid4(), "published", super_admin)

    assert training.status == "published"


# --- TEST 5: rejected/needs_revision must not be able to reach published directly ---

def test_rejected_to_published_is_rejected(monkeypatch):
    training = _training_stub(status="rejected", is_deleted=False)
    _patch_training(monkeypatch, training)

    with pytest.raises(HTTPException) as exc:
        training_service.update_training_status_service(MagicMock(), uuid4(), "published")

    assert exc.value.status_code == 400
    assert training.status == "rejected"


def test_needs_revision_to_published_is_rejected(monkeypatch):
    training = _training_stub(status="needs_revision", is_deleted=False)
    _patch_training(monkeypatch, training)

    with pytest.raises(HTTPException) as exc:
        training_service.update_training_status_service(MagicMock(), uuid4(), "published")

    assert exc.value.status_code == 400
    assert training.status == "needs_revision"


def test_rejected_must_resubmit_through_pending_approval(monkeypatch):
    """The only way out of 'rejected' towards publication is back through
    the draft/pending_approval/approved cycle — never straight to published."""
    training = _training_stub(status="rejected", is_deleted=False)
    _patch_training(monkeypatch, training)

    training_service.update_training_status_service(MagicMock(), uuid4(), "pending_approval")
    assert training.status == "pending_approval"


# --- TEST 6: other pre-existing valid transitions continue to work ---

@pytest.mark.parametrize(
    "start_status,target_status",
    [
        ("pending_approval", "rejected"),
        ("pending_approval", "needs_revision"),
        ("pending_approval", "cancelled"),
        ("approved", "unpublished"),
        ("approved", "draft"),
        ("approved", "cancelled"),
        ("approved", "archived"),
        ("published", "suspended"),
        ("published", "unpublished"),
        ("published", "completed"),
        ("published", "cancelled"),
        ("unpublished", "published"),
        ("unpublished", "draft"),
        ("suspended", "published"),
        ("cancelled", "draft"),
        ("archived", "draft"),
        ("draft", "cancelled"),
        ("draft", "archived"),
    ],
)
def test_pre_existing_valid_transitions_still_work(monkeypatch, start_status, target_status):
    training = _training_stub(status=start_status, is_deleted=False, delivery_mode="self_paced", pass_code=None)
    _patch_training(monkeypatch, training)

    training_service.update_training_status_service(MagicMock(), uuid4(), target_status)

    assert training.status == target_status


def test_archived_restore_to_draft_still_works_when_soft_deleted(monkeypatch):
    """restore_training_service depends on the archived->draft transition
    remaining valid even when is_deleted is set."""
    training = _training_stub(status="archived", is_deleted=True)
    _patch_training(monkeypatch, training)

    training_service.restore_training_service(MagicMock(), uuid4())

    assert training.status == "draft"
    assert training.is_deleted is False
