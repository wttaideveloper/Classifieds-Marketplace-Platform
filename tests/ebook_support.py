"""Shared setup for the eBook tests: an in-memory database, the real routes, a training with an eBook lesson, the people who use it,
real pictures on a temporary upload folder, and notifications captured instead of sent."""
import io
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import ebook, media, training
from app.core.dependencies import get_current_user
from app.db.database import Base, get_db
from app.models import ebook_model
from app.models.enterprise_model import Enterprise
from app.models.notification_model import NotificationEventLog
from app.models.training_model import (
    Training, TrainingAssessmentSubmission, TrainingAssignmentSubmission, TrainingEnrolment, TrainingLessonAttendance, TrainingProgress,
)
from app.services import ebook_notifications, training_upload_service

TENANT_A, TENANT_B = uuid4(), uuid4()
SECTION, LESSON = "sec-1", "les-ebook"
EBOOK_TABLES = [m.__table__ for m in vars(ebook_model).values() if isinstance(m, type) and issubclass(m, Base) and m is not Base]


def person(name, role, tenant=None, **extra):
    return {"id": str(uuid4()), "name": name, "email": f"{name.lower().replace(' ', '.')}@example.test", "role": role, "status": "active",
            "tenant_id": str(tenant) if tenant else None, **extra}


def image_bytes(fmt="PNG", size=(800, 600), color=(40, 120, 200)) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format=fmt)
    return out.getvalue()


def doc(pages=None, **extra):
    base = {"subtitle": "An introduction to nutrition", "author": "Invigorate Health", "description": "Book description", "completion_rule": "read_to_end",
            "allow_bookmarks": True, "allow_notes": True, "pages": pages if pages is not None else [page("page_1", "Introduction"), page("page_2", "Food groups")]}
    base.update(extra)
    return base


def page(page_id, title, html=None, images=None, **extra):
    return {"id": page_id, "title": title, "content_html": html if html is not None else f"<h2>{title}</h2><p>Nutrition is the study of food.</p>", "images": images or [], **extra}


def image_block(asset_id, block_id="image_block_1", **extra):
    return {"id": block_id, "asset_id": asset_id, "alt_text": "Healthy food diagram", "caption": "Examples of healthy food", "width": "full", "fit": "cover",
            "aspect_ratio": "16:9", "position": "center", **extra}


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    tables = EBOOK_TABLES + [m.__table__ for m in (Enterprise, Training, TrainingEnrolment, TrainingProgress, TrainingAssessmentSubmission, TrainingAssignmentSubmission,
                                                   TrainingLessonAttendance, NotificationEventLog)]
    Base.metadata.create_all(engine, tables=tables)
    db = sessionmaker(bind=engine)()
    monkeypatch.setattr(training_upload_service, "training_upload_root", lambda: tmp_path)

    root = person("Platform Root", "super_admin")
    author = person("Asha Author", "admin", TENANT_A)
    provider = person("Dr Provider", "provider", TENANT_A)
    outsider = person("Bela Outsider", "admin", TENANT_B)
    learner = person("Priya Learner", "customer")
    learner2 = person("Ravi Learner", "customer")
    stranger = person("Sam Stranger", "customer")
    people = SimpleNamespace(root=root, author=author, provider=provider, outsider=outsider, learner=learner, learner2=learner2, stranger=stranger)

    enterprise = Enterprise(tenant_id=TENANT_A, business_short_name="Invigorate", business_legal_name="Invigorate Health Pvt Ltd", business_email="inv@example.test")
    db.add(enterprise)
    db.flush()
    sections = [{"id": SECTION, "title": "Module 1", "type": "section", "order": 1, "lessons": [
        {"id": LESSON, "type": "ebook", "title": "Nutrition basics", "order": 1},
        {"id": "les-video", "type": "video", "title": "A video", "order": 2, "content_url": "https://example.test/v.mp4"},
    ]}]
    course = Training(tenant_id=TENANT_A, enterprise_id=enterprise.id, title="Nutrition 101", category="health", status="published", delivery_mode="self_paced", sections=sections)
    db.add(course)
    db.flush()
    for who in (learner, learner2):
        db.add(TrainingEnrolment(training_id=course.id, participant_name=who["name"], participant_email=who["email"], user_id=UUID(who["id"]), status="enrolled",
                                 qr_code=uuid4().hex))
    db.commit()

    notices: list[dict] = []
    monkeypatch.setattr(ebook_notifications, "_dispatch", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr("app.services.notification_service.create_automatic_notification", lambda _db, **kw: notices.append(kw) or SimpleNamespace(id=uuid4()))
    monkeypatch.setattr("app.services.event_notification_service.resolve_platform_admin_user_ids", lambda token=None: [UUID(root["id"])])
    monkeypatch.setattr("app.core.auth_context.resolve_auth_tenant_id_with_db", lambda _db, user, access_token=None: user.get("tenant_id"))

    @contextmanager
    def session_factory():
        yield db

    monkeypatch.setattr("app.db.database.SessionLocal", session_factory)
    app = FastAPI()
    app.include_router(training.router, prefix="/trainings")
    app.include_router(ebook.router, prefix="/trainings")
    app.include_router(ebook.admin_router, prefix="/admin")
    app.include_router(media.router, prefix="/media")
    state = {"user": None}

    def current_user():
        from fastapi import HTTPException

        if state["user"] is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return state["user"]

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = current_user
    client = TestClient(app, raise_server_exceptions=False)

    def as_user(user):
        state["user"] = user
        return client

    w = SimpleNamespace(db=db, client=client, as_user=as_user, people=people, notices=notices, training=course, tmp=tmp_path, enterprise=enterprise,
                        base=f"/trainings/{course.id}/sections/{SECTION}/lessons/{LESSON}/ebook", learner_base=f"/trainings/{course.id}/lessons/{LESSON}/ebook",
                        admin_base=f"/admin/trainings/{course.id}/sections/{SECTION}/lessons/{LESSON}/ebook")
    yield w
    db.close()
    engine.dispose()


# ---------------------------------------------------------------- helpers the tests share

def upload(world, user=None, fmt="PNG", size=(800, 600), name=None, tenant=None):
    """A real picture saved through the same code the upload endpoint uses. Returns the asset id."""
    user = user or world.people.author
    ext = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp", "GIF": "gif"}[fmt]
    result = training_upload_service.save_training_upload(
        image_bytes(fmt, size), name or f"diagram.{ext}", {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}[fmt], "image", world.db, user)
    return result["asset_id"]


def save(world, document=None, user=None, **extra):
    body = {"ebook": document if document is not None else doc(), **extra}
    return world.as_user(user or world.people.author).put(world.base, json=body)


def saved(world, document=None, user=None, **extra):
    resp = save(world, document, user, **extra)
    assert resp.status_code == 200, resp.text
    return resp.json()


def submit(world, user=None):
    return world.as_user(user or world.people.author).post(world.base + "/submit", json={})


def decide(world, action="approve", note=None, publish=True, user=None):
    body = {"action": action, "publish": publish, **({"note": note} if note else {})}
    return world.as_user(user or world.people.root).post(world.admin_base + "/review", json=body)


def go_live(world, document=None):
    """A saved, submitted and approved eBook that learners can read."""
    saved(world, document)
    assert submit(world).status_code == 200
    resp = decide(world)
    assert resp.status_code == 200, resp.text
    return resp.json()


def notices_of(world, category):
    return [n for n in world.notices if n["category"] == category]
