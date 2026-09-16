"""Use real ORM commits and fresh sessions: mocks cannot detect lost JSON edits."""
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker

from app.api.v1.endpoints.training import update_section, update_lesson, delete_lesson_media
from app.db.database import Base
from app.models.enterprise_model import Enterprise
from app.models.training_model import Training
from app.schemas.training_schema import SectionCreate, LessonCreate, AssessmentQuestionCreate
from app.services.training_service import add_assessment_question_service
from app.services.training_curriculum import normalize_authoring


@pytest.fixture(params=[True, False], ids=["expire-on-commit", "retain-on-commit"])
def persisted(request, monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *args, **kwargs: "JSON", raising=False)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Enterprise.__table__, Training.__table__])
    sessions = sessionmaker(bind=engine, expire_on_commit=request.param)
    tid = uuid4()
    with sessions() as db:
        db.add(Training(id=tid, enterprise_id=uuid4(), title="Training", category="General", delivery_mode="physical", venue="Hall",
            sections=[{"id":"s1", "title":"Old title", "type":"section", "order":1, "lessons":[{
                "id":"l1", "title":"Lesson", "type":"topic", "order":1,
                "videos":["https://cdn.example.com/video.mp4"], "notes":["https://cdn.example.com/notes.pdf"],
                "documents":[{"url":"https://cdn.example.com/doc.pdf"}]}]}],
            assessments=[{"id":"a1", "title":"Quiz", "questions":None}], assignments=[]))
        db.commit()
        row = db.get(Training, tid)
        row.sections = normalize_authoring({"sections": row.sections}, row)["sections"]
        db.commit()
    # Isolate persistence from unrelated eager-loading/tenant queries.
    monkeypatch.setattr("app.repository.training_repo.get_training_by_id", lambda db, tid: db.get(Training, tid))
    monkeypatch.setattr("app.services.training_service._get_training_or_404", lambda db, tid: db.get(Training, tid))
    yield sessions, tid
    engine.dispose()


def test_section_title_survives_commit_and_fresh_read(persisted):
    sessions, tid = persisted
    with sessions() as db:
        # An empty assessment list keeps this test focused on the JSON update.
        row = db.get(Training, tid)
        row.assessments = []
        db.commit()
        result = update_section(tid, "s1", SectionCreate(title="New title", type="section"), db, {"role":"admin"})
        assert result["title"] == "New title"
    with sessions() as fresh:
        assert fresh.get(Training, tid).sections[0]["title"] == "New title"


def test_explicit_empty_media_lists_survive_fresh_read(persisted):
    sessions, tid = persisted
    with sessions() as db:
        db.get(Training, tid).assessments = []
        db.commit()
        result = update_lesson(tid, "s1", "l1", LessonCreate(title="Lesson", type="topic", videos=[], notes=[], documents=[]), db, {"role":"admin"})
        assert all(result[key] == [] for key in ("videos", "notes", "documents"))
    with sessions() as fresh:
        lesson = fresh.get(Training, tid).sections[0]["lessons"][0]
        assert all(lesson[key] == [] for key in ("videos", "notes", "documents"))


@pytest.mark.parametrize("kind,url", [("videos","https://cdn.example.com/video.mp4"), ("notes","https://cdn.example.com/notes.pdf"), ("documents","https://cdn.example.com/doc.pdf")])
def test_delete_media_survives_fresh_read(persisted, kind, url):
    sessions, tid = persisted
    with sessions() as db:
        db.get(Training, tid).assessments = []
        db.commit()
        result = delete_lesson_media(tid, "s1", "l1", kind, url, db, {"role":"admin"})
        assert result[kind] == []
    with sessions() as fresh:
        assert fresh.get(Training, tid).sections[0]["lessons"][0][kind] == []


@pytest.mark.parametrize("options", [None, [{"id":"a", "label":"Choice A"}]])
def test_question_append_handles_legacy_null_questions(persisted, options):
    sessions, tid = persisted
    payload = AssessmentQuestionCreate(question_text="Question?", question_type="mcq", options=options)
    with sessions() as db:
        result = add_assessment_question_service(db, tid, "a1", payload)
        assert result["options"] == options
    with sessions() as fresh:
        questions = fresh.get(Training, tid).assessments[0]["questions"]
        assert len(questions) == 1
        assert questions[0]["id"] == result["id"]


def test_question_append_preserves_existing_questions(persisted):
    sessions, tid = persisted
    with sessions() as db:
        row = db.get(Training, tid)
        row.assessments = [{"id":"a1", "questions":[{"id":"existing", "question_text":"Original"}]}]
        db.commit()
        result = add_assessment_question_service(db, tid, "a1", AssessmentQuestionCreate(question_text="New"))
    with sessions() as fresh:
        assert [q["id"] for q in fresh.get(Training, tid).assessments[0]["questions"]] == ["existing", result["id"]]


def test_invalid_stored_questions_returns_400_without_mutation(persisted):
    sessions, tid = persisted
    with sessions() as db:
        row = db.get(Training, tid)
        row.assessments = [{"id":"a1", "questions":{"invalid":"shape"}}]
        db.commit()
        with pytest.raises(HTTPException) as exc:
            add_assessment_question_service(db, tid, "a1", AssessmentQuestionCreate(question_text="New"))
        assert exc.value.status_code == 400
    with sessions() as fresh:
        assert fresh.get(Training, tid).assessments[0]["questions"] == {"invalid":"shape"}
