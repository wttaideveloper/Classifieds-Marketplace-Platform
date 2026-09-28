"""Training Category/Subcategory CRUD — mirrors EventCategory's shape and
behavior for the Super Admin Categories page, in its own training_categories
table (never shared with event_categories)."""
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import training_category as routes
from app.core.dependencies import get_current_user
from app.db.database import Base, get_db
from app.models.training_model import TrainingCategory


@pytest.fixture
def setup(monkeypatch):
    from app.services import super_admin_identity
    monkeypatch.setattr(super_admin_identity, "fetch_internal_user_by_id", lambda *a, **kw: None)
    monkeypatch.setattr(super_admin_identity, "fetch_auth_me_profile", lambda *a, **kw: None)
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[TrainingCategory.__table__])
    sessions = sessionmaker(bind=engine)
    super_admin = {"id": str(uuid4()), "role": "super_admin", "email": "super@example.com"}

    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1/training-categories")

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: super_admin
    with TestClient(app) as client:
        yield sessions, client, super_admin
    engine.dispose()


def test_create_top_level_category(setup):
    sessions, client, super_admin = setup
    resp = client.post("/api/v1/training-categories/", json={"name": "Wellness", "description": "Health & wellbeing"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Wellness"
    assert body["parent_id"] is None
    assert body["description"] == "Health & wellbeing"
    assert body["id"]
    assert body["created_at"]


def test_create_subcategory_under_parent(setup):
    sessions, client, super_admin = setup
    parent = client.post("/api/v1/training-categories/", json={"name": "Wellness"}).json()
    resp = client.post("/api/v1/training-categories/", json={"name": "Yoga", "parent_id": parent["id"]})
    assert resp.status_code == 201, resp.text
    assert resp.json()["parent_id"] == parent["id"]


def test_create_rejects_unknown_parent_id(setup):
    sessions, client, super_admin = setup
    resp = client.post("/api/v1/training-categories/", json={"name": "Yoga", "parent_id": str(uuid4())})
    assert resp.status_code == 404


def test_create_rejects_duplicate_name(setup):
    sessions, client, super_admin = setup
    client.post("/api/v1/training-categories/", json={"name": "Wellness"})
    resp = client.post("/api/v1/training-categories/", json={"name": "Wellness"})
    assert resp.status_code == 400
    assert "already exists" in resp.json()["detail"]


def test_list_returns_flat_list_with_parent_id(setup):
    sessions, client, super_admin = setup
    parent = client.post("/api/v1/training-categories/", json={"name": "Wellness"}).json()
    client.post("/api/v1/training-categories/", json={"name": "Yoga", "parent_id": parent["id"]})
    resp = client.get("/api/v1/training-categories/")
    assert resp.status_code == 200
    names = {c["name"]: c["parent_id"] for c in resp.json()}
    assert names["Wellness"] is None
    assert names["Yoga"] == parent["id"]


def test_list_is_public_no_auth_required(setup):
    sessions, client, super_admin = setup
    from app.core.dependencies import get_current_user as gcu
    client.app.dependency_overrides.pop(gcu, None)
    resp = client.get("/api/v1/training-categories/")
    assert resp.status_code == 200
    client.app.dependency_overrides[gcu] = lambda: super_admin


def test_update_renames_category(setup):
    sessions, client, super_admin = setup
    cat = client.post("/api/v1/training-categories/", json={"name": "Wellness"}).json()
    resp = client.put(f"/api/v1/training-categories/{cat['id']}", json={"name": "Wellbeing"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Wellbeing"


def test_update_rejects_rename_to_duplicate(setup):
    sessions, client, super_admin = setup
    client.post("/api/v1/training-categories/", json={"name": "Wellness"})
    other = client.post("/api/v1/training-categories/", json={"name": "Safety"}).json()
    resp = client.put(f"/api/v1/training-categories/{other['id']}", json={"name": "Wellness"})
    assert resp.status_code == 400


def test_update_unknown_category_404s(setup):
    sessions, client, super_admin = setup
    resp = client.put(f"/api/v1/training-categories/{uuid4()}", json={"name": "X"})
    assert resp.status_code == 404


def test_delete_removes_category(setup):
    sessions, client, super_admin = setup
    cat = client.post("/api/v1/training-categories/", json={"name": "Wellness"}).json()
    resp = client.delete(f"/api/v1/training-categories/{cat['id']}")
    assert resp.status_code == 200
    assert client.get("/api/v1/training-categories/").json() == []


def test_delete_blocked_while_subcategories_exist(setup):
    sessions, client, super_admin = setup
    parent = client.post("/api/v1/training-categories/", json={"name": "Wellness"}).json()
    client.post("/api/v1/training-categories/", json={"name": "Yoga", "parent_id": parent["id"]})
    resp = client.delete(f"/api/v1/training-categories/{parent['id']}")
    assert resp.status_code == 400
    assert "subcategories" in resp.json()["detail"]


def test_delete_unknown_category_404s(setup):
    sessions, client, super_admin = setup
    resp = client.delete(f"/api/v1/training-categories/{uuid4()}")
    assert resp.status_code == 404


@pytest.mark.parametrize("role", ["admin", "provider", "customer"])
def test_mutations_reject_non_super_admin(setup, role):
    sessions, client, super_admin = setup
    non_super = {"id": str(uuid4()), "role": role, "email": "x@example.com"}
    client.app.dependency_overrides[get_current_user] = lambda: non_super
    assert client.post("/api/v1/training-categories/", json={"name": "X"}).status_code == 403
    cat_id = str(uuid4())
    assert client.put(f"/api/v1/training-categories/{cat_id}", json={"name": "Y"}).status_code == 403
    assert client.delete(f"/api/v1/training-categories/{cat_id}").status_code == 403
    client.app.dependency_overrides[get_current_user] = lambda: super_admin


def test_training_and_event_categories_are_separate_tables(setup):
    """Sanity check the taxonomy is genuinely independent — same name allowed
    in both, deleting one never touches the other (enforced simply by using
    a distinct table; this asserts the model/table identity, not behavior)."""
    from app.models.event_aux_models import EventCategory
    assert TrainingCategory.__tablename__ == "training_categories"
    assert EventCategory.__tablename__ == "event_categories"
    assert TrainingCategory.__tablename__ != EventCategory.__tablename__
