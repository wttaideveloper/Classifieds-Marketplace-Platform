"""GET /trainings/{id}/moderation-history: every entry carries its own UTC timestamp."""
import re
from datetime import datetime, timedelta
from uuid import uuid4

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
from app.models.training_model import Training
from app.services import training_notifications, training_service as service, training_workflow_notifications as workflow

ISO_UTC_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
TID = uuid4()
TENANT = uuid4()
ADMIN = {"id": str(uuid4()), "role": "admin", "email": "owner@acme.example", "tenant_id": str(TENANT)}
ROOT = {"id": str(uuid4()), "role": "super_admin", "email": "root@platform.example"}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    # state changes below would notify admins over the network — not what is under test
    monkeypatch.setattr(workflow, "notify_training_approval", lambda *a, **k: None)
    monkeypatch.setattr(training_notifications, "notify_new_training", lambda *a, **k: None)

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[Enterprise.__table__, Training.__table__])
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        db.add(Training(
            id=TID, enterprise_id=uuid4(), tenant_id=TENANT, title="Course", category="Wellness",
            status="draft", delivery_mode="online", moderation_history=[],
        ))
        db.commit()

    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1/trainings")

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    with TestClient(app) as client:
        yield sessions, client
    engine.dispose()


def history(client):
    resp = client.get(f"/api/v1/trainings/{TID}/moderation-history")
    assert resp.status_code == 200, resp.text
    return resp.json()


def move(sessions, status, actor, notes=None):
    with sessions() as db:
        service.update_training_status_service(db, TID, status, actor, notes=notes)


def test_every_workflow_action_has_its_own_utc_timestamp(env):
    sessions, client = env
    started = datetime.utcnow() - timedelta(seconds=1)
    move(sessions, "pending_approval", ADMIN)
    move(sessions, "needs_revision", ROOT, "Add an agenda")
    move(sessions, "pending_approval", ADMIN)
    move(sessions, "rejected", ROOT, "Out of scope")
    move(sessions, "draft", ADMIN)
    move(sessions, "pending_approval", ADMIN)
    move(sessions, "approved", ROOT)
    move(sessions, "published", ROOT)
    move(sessions, "unpublished", ADMIN)
    finished = datetime.utcnow() + timedelta(seconds=1)

    items = history(client)
    assert [i["action"] for i in items] == [
        "pending_approval", "needs_revision", "pending_approval", "rejected", "draft",
        "pending_approval", "approved", "published", "unpublished",
    ]
    stamps = []
    for item in items:
        assert ISO_UTC_Z.match(item["created_at"]), item
        moment = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00")).replace(tzinfo=None)
        assert started <= moment <= finished  # the time the action happened, not some other date
        stamps.append(moment)
    assert stamps == sorted(stamps)  # oldest first

    by_action = {i["action"]: i for i in items}
    for action in ("approved", "rejected", "needs_revision", "published", "unpublished"):
        assert by_action[action]["created_at"]
    assert by_action["rejected"]["reason"] == "Out of scope" and by_action["rejected"]["actor_role"] == "super_admin"
    assert by_action["published"]["previous_status"] == "approved" and by_action["published"]["new_status"] == "published"


def test_entries_get_different_timestamps_not_the_trainings_updated_at(env):
    sessions, client = env
    move(sessions, "pending_approval", ADMIN)
    first = history(client)[0]["created_at"]
    # an unrelated edit moves the Training's updated_at but must not touch any history entry
    with sessions() as db:
        db.get(Training, TID).title = "Renamed"
        db.commit()
    move(sessions, "approved", ROOT)
    items = history(client)
    assert items[0]["created_at"] == first
    assert items[1]["created_at"] >= first


def test_the_legacy_at_field_is_kept_and_matches(env):
    sessions, client = env
    move(sessions, "pending_approval", ADMIN)
    item = history(client)[0]
    assert item["at"] and item["created_at"] == item["at"] + "Z"  # `at` is UTC without the designator


def test_entries_already_stored_are_covered_naive_utc_offset_aware_and_odd_shapes(env):
    """Rows written by every past version: `at` = utcnow().isoformat(), possibly an offset, possibly nothing."""
    sessions, client = env
    with sessions() as db:
        db.get(Training, TID).moderation_history = [
            {"action": "approved", "reason": None, "actor_email": "a@x.example", "actor_role": "admin", "at": "2026-09-03T08:15:30.250000"},
            {"action": "rejected", "reason": "no", "at": "2026-09-04T13:45:00+05:30"},
            {"action": "published", "at": "2026-09-05T00:00:00Z"},
            {"action": "unpublished"},                       # no timestamp at all
            {"action": "approved", "at": "not a date"},      # unparseable
            "stray string entry",
        ]
        db.commit()

    items = history(client)
    assert [i["created_at"] for i in items] == [
        "2026-09-03T08:15:30.250000Z",
        "2026-09-04T08:15:00Z",       # converted from +05:30 to UTC
        "2026-09-05T00:00:00Z",
        None, None, None,
    ]
    assert items[0]["at"] == "2026-09-03T08:15:30.250000"   # stored value untouched
    assert items[5]["action"] == "stray string entry"        # tolerated, not a 500


def test_empty_history_is_an_empty_list(env):
    _, client = env
    assert history(client) == []


def test_openapi_documents_created_at(env):
    from app.main import app

    schema = app.openapi()["components"]["schemas"]["TrainingModerationHistoryItem"]
    assert "created_at" in schema["properties"] and "UTC" in schema["properties"]["created_at"]["description"]
