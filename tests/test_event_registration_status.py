"""GET /events/{id} — is_registered / registration_status for a signed-in
caller (mirrors the Training is_enrolled/enrolment_status addition), while
staying public/anonymous-friendly."""
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import event as event_routes
from app.core.dependencies import get_current_user, get_optional_current_user
from app.db.database import Base, get_db
from app.models.enterprise_model import Enterprise
from app.models.event_aux_models import EventRegistration
from app.models.event_model import Event


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[Enterprise.__table__, Event.__table__, EventRegistration.__table__])
    sessions = sessionmaker(bind=engine)
    eid = uuid4()
    with sessions() as db:
        db.add(Event(id=eid, title="Conference", category="Business", status="published"))
        db.commit()

    app = FastAPI()
    app.include_router(event_routes.router, prefix="/api/v1/events")

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        yield sessions, client, eid
    engine.dispose()


def test_anonymous_request_still_works_and_is_not_registered(setup):
    sessions, client, eid = setup
    resp = client.get(f"/api/v1/events/{eid}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_registered"] is False
    assert body["registration_status"] is None


def test_signed_in_never_registered(setup):
    sessions, client, eid = setup
    user = {"id": str(uuid4()), "role": "customer", "email": "learner@example.com"}
    client.app.dependency_overrides[get_optional_current_user] = lambda: user
    resp = client.get(f"/api/v1/events/{eid}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_registered"] is False
    assert body["registration_status"] is None


def test_signed_in_confirmed_registration(setup):
    sessions, client, eid = setup
    user = {"id": str(uuid4()), "role": "customer", "email": "learner@example.com"}
    with sessions() as db:
        db.add(EventRegistration(event_id=eid, participant_name="Learner", participant_email=user["email"], status="confirmed"))
        db.commit()
    client.app.dependency_overrides[get_optional_current_user] = lambda: user
    resp = client.get(f"/api/v1/events/{eid}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_registered"] is True
    assert body["registration_status"] == "confirmed"


def test_signed_in_cancelled_registration_is_not_registered(setup):
    sessions, client, eid = setup
    user = {"id": str(uuid4()), "role": "customer", "email": "learner@example.com"}
    with sessions() as db:
        db.add(EventRegistration(event_id=eid, participant_name="Learner", participant_email=user["email"], status="cancelled"))
        db.commit()
    client.app.dependency_overrides[get_optional_current_user] = lambda: user
    resp = client.get(f"/api/v1/events/{eid}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_registered"] is False
    assert body["registration_status"] == "cancelled"


def test_invalid_bearer_token_still_rejected_not_silently_anonymous(setup):
    """get_optional_current_user must only skip auth when NO credentials are
    supplied — a garbage/expired token must still fail loudly, not degrade
    to an anonymous 200."""
    sessions, client, eid = setup
    resp = client.get(f"/api/v1/events/{eid}", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401
