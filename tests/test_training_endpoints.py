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
