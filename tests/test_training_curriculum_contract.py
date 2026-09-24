from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.training_model import Training
from app.schemas.training_schema import TrainingCreate, TrainingUpdate, SectionCreate, AssessmentQuestionCreate
from app.services import training_service
from app.services.training_curriculum import normalize_authoring, normalize_curriculum, curriculum_preview
from app.services.response_mappers import map_training_write
from tests.test_training_secure_content_v2 import _mock_db


def curriculum(mode):
    items = [{"id": "topic", "type": "topic", "title": "Safety", "content": "Read this", "is_mandatory": True}]
    if mode in ("physical", "hybrid"):
        items.append({"id": "venue", "type": "venue", "title": "Practice", "venue": "Hall", "address": "Chennai", "check_in_window": "9-10"})
    if mode in ("online", "hybrid"):
        items.append({"id": "live", "type": "live", "title": "Live", "join_url": "https://meet.example.com/room", "join_meta": "Secret", "duration_minutes": 60})
    if mode == "recorded":
        items.append({"id": "video", "type": "video", "title": "Video", "video_url": "https://cdn.example.com/video.mp4", "duration_minutes": 14})
    items += [
        {"id": "pdf", "type": "pdf", "title": "Handbook", "content_url": "https://cdn.example.com/book.pdf", "file_size": "420 KB", "is_downloadable": True},
        {"id": "task", "type": "assignment", "title": "Task", "assignment": {"id": "a1", "kind": "text", "instructions": "Write steps", "max_score": 10}},
        {"id": "quiz", "type": "quiz", "title": "Quiz", "is_mandatory": True,
         "assessment": {"id": "qz", "pass_percent": 70, "questions": [{"id": "q1", "question_text": "First step?", "question_type": "single_choice", "options": [{"id": "a", "label": "Safety"}, {"id": "b", "label": "Run"}], "correct_answer": "a"}]}},
    ]
    return [{"id": "s1", "title": "Session", "schedule": "2026-09-20T09:00:00+05:30", "items": items}]


@pytest.mark.parametrize("mode", ["physical", "online", "hybrid", "recorded"])
def test_admin_create_and_read_round_trip(mode):
    data = TrainingCreate(enterprise_id=uuid4(), title="First aid", category="Safety", delivery_mode=mode,
                          venue="Hall", sections=curriculum(mode), notes_pdf_url="https://cdn.example.com/notes.pdf",
                          documents=[{"id": "doc", "title": "Handbook", "url": "https://cdn.example.com/book.pdf", "downloadable": True}],
                          notes_documents=[{"id": "note", "url": "https://cdn.example.com/notes.pdf", "downloadable": True}])
    payload = normalize_authoring(data.to_model_data())
    training = Training(id=uuid4(), **payload)
    response = map_training_write(training)
    items = response["sections"][0]["items"]
    assert items == response["sections"][0]["lessons"]
    assert [i["order"] for i in items] == list(range(1, len(items) + 1))
    assert {i["type"] for i in items} >= {"topic", "pdf", "assignment", "quiz"}
    assert response["notes_pdf_url"] == data.notes_pdf_url
    assert response["notes_documents"][0]["id"] == "note"
    assert response["assignments"][0]["id"] == "a1"
    assert response["assessments"][0]["questions"][0]["options"][0] == {"id": "a", "label": "Safety"}
    assert response["check_in"] is (mode in ("physical", "hybrid"))
    if mode == "recorded":
        assert response["sections"][0]["schedule"] is None
        assert next(i for i in items if i["type"] == "video")["content_url"].endswith("video.mp4")


@pytest.mark.parametrize("mode", ["physical", "online", "hybrid", "recorded"])
def test_learning_shape_progress_and_no_answer_leak(mode, monkeypatch):
    payload = normalize_authoring(TrainingCreate(enterprise_id=uuid4(), title="First aid", category="Safety",
        delivery_mode=mode, sections=curriculum(mode), venue="Hall").to_model_data())
    training = Training(id=uuid4(), **payload)
    training.status = "published"
    monkeypatch.setattr(training_service, "_get_training_or_404", lambda *args: training)
    monkeypatch.setattr(training_service, "_lesson_is_accessible", lambda *args: (True, None))
    result = training_service.get_secure_training_content_service(_mock_db(progress_lessons=["topic"]), training.id, {"email": "me@example.com", "role": "learner"})
    section = result["sections"][0]
    assert section["items"] == section["lessons"]
    assert result["total_required_items"] == 2
    assert result["completed_required_items"] == 1
    # progress_percent is the raw completed/total lesson ratio (single source of
    # truth shared with /my/enrolments) — mandatory-only completion is a separate
    # rule, verified above via total_required_items/completed_required_items.
    assert result["progress_percent"] == round(result["completed_lessons"] / result["total_lessons"] * 100, 2)
    quiz = next(i for i in section["items"] if i["type"] == "quiz")
    assert quiz["is_locked"] is False  # optional PDF/task does not gate a required quiz
    assert "correct_answer" not in quiz["assessment"]["questions"][0]
    assert (result["qr_code"] is not None) is (mode in ("physical", "hybrid"))
    # "live" is also a QR/attendance-capable kind in venue-capable modes (hybrid can
    # be scanned in on-site), matching venue's own qr_code — not just "venue" items.
    for item in section["items"]:
        if item["type"] in ("live", "venue") and mode in ("physical", "hybrid"):
            assert item["qr_code"] == result["qr_code"]
        else:
            assert item["qr_code"] is None


def test_preview_withholds_credentials_and_paid_content():
    normalized = normalize_curriculum(curriculum("hybrid"))
    preview = curriculum_preview(normalized["sections"])
    for item in preview[0]["items"]:
        assert item["meeting_link"] is None
        assert item["join_meta"] is None
        assert item.get("content_url") is None
        if item.get("assessment"):
            assert item["assessment"]["questions"] == []


def test_invalid_choice_and_search_url_rejected():
    bad = curriculum("recorded")
    bad[0]["items"][1]["video_url"] = "https://www.youtube.com/results?search_query=safety"
    with pytest.raises(HTTPException):
        normalize_curriculum(bad, strict=True)
    bad = curriculum("recorded")
    bad[0]["items"][-1]["assessment"]["questions"][0]["options"] = []
    with pytest.raises(HTTPException):
        normalize_curriculum(bad, strict=True)


def test_update_preserves_existing_assessments_and_ignores_client_progress():
    initial = normalize_authoring({"sections": curriculum("physical"), "delivery_mode": "physical"})
    existing = SimpleNamespace(**initial)
    sections = deepcopy(existing.sections)
    sections[0]["lessons"][0]["is_completed"] = True
    changes = normalize_authoring(TrainingUpdate(sections=sections).to_model_data(), existing)
    assert "is_completed" not in changes["sections"][0]["lessons"][0]
    assert changes["assessments"][0]["id"] == "qz"


def test_builder_accepts_iso_schedule_and_object_options():
    assert SectionCreate(title="Session", schedule="2026-09-20T09:00:00Z").schedule.endswith("Z")
    assert AssessmentQuestionCreate(question_text="First?", question_type="single_choice", options=[{"id": "a", "label": "Safety"}]).options[0]["id"] == "a"


@pytest.mark.parametrize("prefix", ["trainings", "courses"])
@pytest.mark.parametrize("mode", ["physical", "online", "hybrid", "recorded"])
def test_http_create_and_admin_get_have_same_curriculum(prefix, mode, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.v1.endpoints.training import router
    from app.core.dependencies import get_current_user
    from app.db.database import get_db

    app = FastAPI()
    app.include_router(router, prefix=f"/api/v1/{prefix}")
    db = MagicMock()
    saved = []
    db.add.side_effect = saved.append
    db.refresh.side_effect = lambda obj: setattr(obj, "id", obj.id or uuid4())
    db.query.return_value.filter.return_value.count.return_value = 0
    db.query.return_value.filter.return_value.all.return_value = []
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: {"role": "admin", "email": "admin@example.com"}
    monkeypatch.setattr(training_service, "_validate", lambda *args: None)
    monkeypatch.setattr("app.services.training_form_config_service.apply_form_configuration_to_training_data", lambda *args: {})
    monkeypatch.setattr(training_service, "get_training_by_id", lambda *args: saved[0])
    client = TestClient(app)
    payload = {"tenant_id": str(uuid4()), "enterprise_id": str(uuid4()), "title": "First Aid", "category": "Safety",
               "delivery_mode": mode, "venue": "Hall", "sections": curriculum(mode)}
    created = client.post(f"/api/v1/{prefix}/", json=payload)
    assert created.status_code == 201, created.text
    detail = client.get(f"/api/v1/{prefix}/{created.json()['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["sections"] == created.json()["sections"]
    assert detail.json()["assessments"] == created.json()["assessments"]
    assert detail.json()["assignments"] == created.json()["assignments"]
