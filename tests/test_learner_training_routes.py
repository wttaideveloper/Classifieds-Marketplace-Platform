from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import my_trainings, training
from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.services import training_service as service


def test_review_uses_authenticated_identity_and_returns_403(monkeypatch):
    app = FastAPI()
    app.include_router(training.router, prefix="/api/v1/trainings")
    app.dependency_overrides[get_current_user] = lambda: {"email": "caller@example.com"}
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(service, "_get_training_or_404", lambda *args: object())
    observed = []
    def review(db, tid, payload):
        observed.append(payload.participant_email)
        return service.create_training_review_service(db, tid, payload)
    monkeypatch.setattr(training, "create_training_review_service", review)
    response = TestClient(app).post(f"/api/v1/trainings/{uuid4()}/reviews", json={
        "rating": 5, "participant_email": "someone-else@example.com",
    })
    assert observed == ["caller@example.com"]
    assert response.status_code == 403
    assert response.json() == {"detail": "Verified reviews only — must be enrolled to review"}


@pytest.mark.parametrize("existing", [False, True])
def test_wishlist_toggle(existing, monkeypatch):
    monkeypatch.setattr(service, "_get_training_or_404", lambda *args: object())
    db = MagicMock()
    item = object() if existing else None
    db.query.return_value.filter.return_value.first.return_value = item
    result = service.toggle_training_wishlist_service(db, uuid4(), uuid4())
    assert result["wishlisted"] is (not existing)
    assert db.delete.call_count == int(existing)
    assert db.add.call_count == int(not existing)
    db.commit.assert_called_once()


def test_qr_requires_own_active_enrolment(monkeypatch):
    monkeypatch.setattr(service, "_get_training_or_404", lambda *args: object())
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    with pytest.raises(Exception) as exc:
        service.show_training_qr_service(db, uuid4(), {"email": "caller@example.com"})
    assert exc.value.status_code == 403


def test_qr_renders_enrolment_code(monkeypatch):
    monkeypatch.setattr(service, "_get_training_or_404", lambda *args: object())
    monkeypatch.setattr(service, "_learner_enrolment", lambda *args: SimpleNamespace(qr_code="door-code"))
    validate = MagicMock()
    monkeypatch.setattr(service, "validate_training_qr_service", validate)
    monkeypatch.setattr(service, "_qr_image_base64", lambda code: "image:" + code)
    tid = uuid4()
    db = MagicMock()
    result = service.show_training_qr_service(db, tid, {"email": "caller@example.com"})
    assert result["qr_image_base64"] == "image:door-code"
    validate.assert_called_once_with(db, tid, "door-code")


def test_me_routes_registered():
    routes = {(route.path, method) for route in my_trainings.router.routes for method in route.methods}
    assert ("/trainings", "GET") in routes
    assert ("/trainings/{training_id}/qr-show", "POST") in routes
    assert ("/trainings/{training_id}/wishlist", "POST") in routes

@pytest.mark.parametrize("enrolled", [False, True])
def test_detail_gates_private_fields_and_uses_secure_sections(monkeypatch, enrolled):
    private = {key: "secret" for key in ("meeting_link", "meeting_passcode", "qr_payload", "notes_pdf_url")}
    raw = {**private, "sections": [{"content_url": "secret"}], "assessments": [{"answer": "secret"}], "assignments": []}
    monkeypatch.setattr(service, "get_training_service", lambda *args: SimpleNamespace(model_dump=lambda: raw.copy()))
    monkeypatch.setattr(service, "_learner_enrolment", lambda *args: object() if enrolled else None)
    monkeypatch.setattr(service, "TrainingDetailResponse", SimpleNamespace(model_validate=lambda value: value))
    secure = MagicMock(return_value={"sections": [{"is_unlocked": False}]})
    monkeypatch.setattr(service, "get_secure_training_content_service", secure)
    result = service.get_learner_training_detail_service(MagicMock(), uuid4(), {"email": "caller@example.com", "role": "learner"})
    assert result["assessments"] == []
    if enrolled:
        assert result["sections"] == [{"is_unlocked": False}]
        secure.assert_called_once()
    else:
        assert result["sections"][0]["content_url"] is None
        assert result["sections"][0]["items"] == []
        assert all(result[key] is None for key in private)
        secure.assert_not_called()
