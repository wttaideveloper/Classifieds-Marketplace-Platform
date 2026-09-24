from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import training as routes
from app.core.dependencies import get_current_user
from app.db.database import Base, get_db
from app.models.enterprise_model import Enterprise
from app.models.training_model import (
    Training,
    TrainingAssessmentSubmission,
    TrainingAssignmentSubmission,
    TrainingEnrolment,
    TrainingProgress,
)

TID = uuid4()
SEC1 = "session-1-id"
SEC2 = "session-2-id"
LESSON1 = "lesson-1-id"
LESSON2 = "lesson-2-id"


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[
        m.__table__ for m in (
            Enterprise, Training, TrainingEnrolment, TrainingProgress,
            TrainingAssessmentSubmission, TrainingAssignmentSubmission,
        )
    ])
    sessions = sessionmaker(bind=engine)
    tenant_id = uuid4()
    user = {"id": str(uuid4()), "role": "admin", "email": "admin@example.com", "tenant_id": str(tenant_id)}
    sections = [
        {
            "id": SEC1, "type": "section", "title": "Session 1",
            "lessons": [
                {"id": LESSON1, "type": "topic", "title": "Lesson 1"},
                {"id": LESSON2, "type": "topic", "title": "Lesson 2"},
            ],
        },
        {"id": SEC2, "type": "section", "title": "Session 2", "lessons": []},
    ]
    with sessions() as db:
        db.add(Training(
            id=TID, enterprise_id=uuid4(), tenant_id=tenant_id, title="Course", category="Wellness",
            status="published", delivery_mode="online", sections=sections, assessments=[], assignments=[],
        ))
        db.commit()
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1/trainings")

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as client:
        yield sessions, client
    engine.dispose()


def test_reorder_sections_persists_and_is_reflected_on_get(setup):
    sessions, client = setup
    url = f"/api/v1/trainings/{TID}/sections/reorder"
    resp = client.post(url, json={"section_orders": [{"id": SEC2, "order": 0}, {"id": SEC1, "order": 1}]})
    assert resp.status_code == 200, resp.text
    assert [s["id"] for s in resp.json()] == [SEC2, SEC1]

    # "Refresh the page" — a fresh GET must show the new order too.
    content = client.get(f"/api/v1/trainings/{TID}/content")
    assert content.status_code == 200
    assert [s["id"] for s in content.json()["sections"]] == [SEC2, SEC1]


def test_reorder_lessons_persists_and_is_reflected_on_get(setup):
    sessions, client = setup
    url = f"/api/v1/trainings/{TID}/sections/{SEC1}/lessons/reorder"
    resp = client.post(url, json={"order": [LESSON2, LESSON1]})
    assert resp.status_code == 200, resp.text
    assert [l["id"] for l in resp.json()] == [LESSON2, LESSON1]

    content = client.get(f"/api/v1/trainings/{TID}/content")
    assert content.status_code == 200
    section = next(s for s in content.json()["sections"] if s["id"] == SEC1)
    assert [l["id"] for l in section["lessons"]] == [LESSON2, LESSON1]


def test_reorder_lessons_accepts_lesson_orders_format(setup):
    sessions, client = setup
    url = f"/api/v1/trainings/{TID}/sections/{SEC1}/lessons/reorder"
    resp = client.post(url, json={"lesson_orders": [{"id": LESSON2, "order": 0}, {"id": LESSON1, "order": 1}]})
    assert resp.status_code == 200, resp.text
    assert [l["id"] for l in resp.json()] == [LESSON2, LESSON1]


def test_reorder_sections_rejects_id_from_another_training(setup):
    sessions, client = setup
    foreign_id = str(uuid4())
    url = f"/api/v1/trainings/{TID}/sections/reorder"
    resp = client.post(url, json={"section_orders": [{"id": foreign_id, "order": 0}]})
    assert resp.status_code == 422, resp.text


def test_reorder_lessons_rejects_id_from_another_section(setup):
    sessions, client = setup
    url = f"/api/v1/trainings/{TID}/sections/{SEC2}/lessons/reorder"
    # LESSON1 belongs to SEC1, not SEC2 — must be rejected, not silently ignored.
    resp = client.post(url, json={"order": [LESSON1]})
    assert resp.status_code == 422, resp.text


def test_reorder_sections_rejects_empty_payload(setup):
    sessions, client = setup
    url = f"/api/v1/trainings/{TID}/sections/reorder"
    resp = client.post(url, json={"section_orders": []})
    assert resp.status_code == 422, resp.text


def test_reorder_lessons_rejects_missing_both_formats(setup):
    sessions, client = setup
    url = f"/api/v1/trainings/{TID}/sections/{SEC1}/lessons/reorder"
    resp = client.post(url, json={})
    assert resp.status_code == 422, resp.text


def test_reorder_sections_rejects_duplicate_ids(setup):
    sessions, client = setup
    url = f"/api/v1/trainings/{TID}/sections/reorder"
    resp = client.post(url, json={"section_orders": [{"id": SEC1, "order": 0}, {"id": SEC1, "order": 1}]})
    assert resp.status_code == 422, resp.text


def test_reorder_sections_unknown_training_404s(setup):
    sessions, client = setup
    url = f"/api/v1/trainings/{uuid4()}/sections/reorder"
    resp = client.post(url, json={"section_orders": [{"id": SEC1, "order": 0}]})
    assert resp.status_code == 404, resp.text


def test_reorder_lessons_unknown_section_404s(setup):
    sessions, client = setup
    url = f"/api/v1/trainings/{TID}/sections/{uuid4()}/lessons/reorder"
    resp = client.post(url, json={"order": [LESSON1]})
    assert resp.status_code == 404, resp.text


def test_reorder_sections_partial_list_keeps_remaining_after(setup):
    """Only reordering SEC2 to front still preserves SEC1 (not listed) afterward."""
    sessions, client = setup
    url = f"/api/v1/trainings/{TID}/sections/reorder"
    resp = client.post(url, json={"section_orders": [{"id": SEC2, "order": 0}]})
    assert resp.status_code == 200, resp.text
    assert [s["id"] for s in resp.json()] == [SEC2, SEC1]
