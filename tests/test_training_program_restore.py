"""Unit tests for archived → draft restore transitions."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.program_service import update_program_status_service
from app.services.training_service import update_training_status_service


def _training(status="archived", is_deleted=False):
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=None,
        status=status,
        is_deleted=is_deleted,
        last_admin_notes=None,
        __table__=SimpleNamespace(columns=[]),
    )


def _program(status="archived", is_deleted=False):
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=None,
        status=status,
        is_deleted=is_deleted,
        last_admin_notes=None,
        __table__=SimpleNamespace(columns=[]),
    )


def test_archived_training_can_restore_to_draft(monkeypatch):
    obj = _training(status="archived", is_deleted=True)
    db = MagicMock()
    monkeypatch.setattr(
        "app.services.training_service.get_training_by_id",
        lambda *_a, **_k: obj,
    )
    monkeypatch.setattr(
        "app.services.training_service.map_training_write",
        lambda t: {"id": str(t.id), "status": t.status, "is_deleted": t.is_deleted},
    )
    monkeypatch.setattr(
        "app.services.training_service.TrainingResponse.model_validate",
        lambda data: data,
    )

    result = update_training_status_service(db, obj.id, "draft")
    assert obj.status == "draft"
    assert obj.is_deleted is False
    assert result["status"] == "draft"


def test_archived_training_cannot_go_to_unpublished(monkeypatch):
    obj = _training(status="archived")
    db = MagicMock()
    monkeypatch.setattr(
        "app.services.training_service.get_training_by_id",
        lambda *_a, **_k: obj,
    )

    with pytest.raises(HTTPException) as exc:
        update_training_status_service(db, obj.id, "unpublished")
    assert exc.value.status_code == 400
    assert "draft" in exc.value.detail


def test_archived_program_can_restore_to_draft(monkeypatch):
    obj = _program(status="archived", is_deleted=True)
    db = MagicMock()
    monkeypatch.setattr(
        "app.services.program_service.get_program_by_id",
        lambda *_a, **_k: obj,
    )
    monkeypatch.setattr(
        "app.services.program_service.map_program_write",
        lambda p: {"id": str(p.id), "status": p.status, "is_deleted": p.is_deleted},
    )
    monkeypatch.setattr(
        "app.services.program_service.ProgramResponse.model_validate",
        lambda data: data,
    )

    result = update_program_status_service(db, obj.id, "draft")
    assert obj.status == "draft"
    assert obj.is_deleted is False
    assert result["status"] == "draft"
