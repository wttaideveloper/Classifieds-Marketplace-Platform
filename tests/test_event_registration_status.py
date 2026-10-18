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
from app.models.event_aux_models import EventFeedback, EventOrder, EventRegistration
from app.models.event_model import Event


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[Enterprise.__table__, Event.__table__, EventRegistration.__table__, EventOrder.__table__, EventFeedback.__table__])
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


# --- GET /events/ — boolean registration_status on every list item ------------------------------------

def _list(client, **params):
    return client.get("/api/v1/events/", params={"status": "published", "page": 1, "page_size": 50, **params})


def _flags(resp):
    assert resp.status_code == 200, resp.text
    return {i["title"]: i["registration_status"] for i in resp.json()["items"]}


@pytest.fixture
def listing(setup):
    sessions, client, _ = setup
    ids = {}
    with sessions() as db:
        for name in ("A", "B", "C", "D"):
            ids[name] = uuid4()
            db.add(Event(id=ids[name], title=f"Event {name}", category="Business", status="published"))
        db.commit()
    return sessions, client, ids


def _register(sessions, event_id, email, status):
    with sessions() as db:
        db.add(EventRegistration(event_id=event_id, participant_name="P", participant_email=email, status=status))
        db.commit()


def test_list_is_true_only_for_events_the_caller_is_registered_for(listing):
    sessions, client, ids = listing
    me = {"id": str(uuid4()), "role": "customer", "email": "me@example.com"}
    _register(sessions, ids["A"], me["email"], "confirmed")
    _register(sessions, ids["B"], me["email"], "attended")
    client.app.dependency_overrides[get_optional_current_user] = lambda: me

    flags = _flags(_list(client))

    assert flags["Event A"] is True and flags["Event B"] is True
    assert flags["Event C"] is False and flags["Event D"] is False


def test_list_ignores_cancelled_no_show_and_other_unsuccessful_registrations(listing):
    sessions, client, ids = listing
    me = {"id": str(uuid4()), "role": "customer", "email": "me@example.com"}
    for name, status in (("A", "cancelled"), ("B", "no_show"), ("C", "pending"), ("D", "rejected")):
        _register(sessions, ids[name], me["email"], status)
    client.app.dependency_overrides[get_optional_current_user] = lambda: me

    assert set(_flags(_list(client)).values()) == {False}


def test_one_users_registration_does_not_affect_another(listing):
    sessions, client, ids = listing
    _register(sessions, ids["A"], "someone.else@example.com", "confirmed")
    me = {"id": str(uuid4()), "role": "customer", "email": "me@example.com"}
    client.app.dependency_overrides[get_optional_current_user] = lambda: me
    assert set(_flags(_list(client)).values()) == {False}

    other = {"id": str(uuid4()), "role": "customer", "email": "someone.else@example.com"}
    client.app.dependency_overrides[get_optional_current_user] = lambda: other
    assert _flags(_list(client))["Event A"] is True


def test_list_for_a_user_with_no_registrations_and_for_anonymous_callers(listing):
    sessions, client, ids = listing
    _register(sessions, ids["A"], "someone@example.com", "confirmed")

    anonymous = _flags(_list(client))                       # no token: public browsing keeps working
    client.app.dependency_overrides[get_optional_current_user] = lambda: {"id": str(uuid4()), "role": "customer", "email": "new@example.com"}
    fresh = _flags(_list(client))

    assert set(anonymous.values()) == {False} and len(anonymous) == 5
    assert set(fresh.values()) == {False}


def test_list_registration_lookup_is_one_query_for_the_whole_page(listing):
    from sqlalchemy import event as sa_event

    sessions, client, ids = listing
    me = {"id": str(uuid4()), "role": "customer", "email": "me@example.com"}
    for name in ids:
        _register(sessions, ids[name], me["email"], "confirmed")
    ids["Conference"] = None   # seeded by `setup`, not registered
    client.app.dependency_overrides[get_optional_current_user] = lambda: me
    engine = sessions.kw["bind"]
    statements = []
    listener = lambda conn, cur, stmt, *a: statements.append(stmt)
    sa_event.listen(engine, "before_cursor_execute", listener)
    try:
        resp = _list(client)
    finally:
        sa_event.remove(engine, "before_cursor_execute", listener)

    flags = _flags(resp)
    assert [t for t, v in flags.items() if not v] == ["Conference"] and sum(flags.values()) == 4
    lookups = [s for s in statements if "participant_email" in s and "event_registrations.event_id IN" in s]
    assert len(lookups) == 1
    # the review numbers on the page are also one query, not one per event
    assert len([s for s in statements if "FROM event_feedback" in s]) == 1


def test_list_keeps_existing_fields_pagination_and_the_detail_contract(listing):
    sessions, client, ids = listing
    me = {"id": str(uuid4()), "role": "customer", "email": "me@example.com"}
    _register(sessions, ids["A"], me["email"], "confirmed")
    client.app.dependency_overrides[get_optional_current_user] = lambda: me

    body = _list(client, page_size=2).json()

    assert len(body["items"]) == 2 and body["pagination"]["total"] == 5 and body["pagination"]["total_pages"] == 3
    assert {"id", "title", "status", "category", "available_seats", "is_full"} <= set(body["items"][0])
    assert client.get(f"/api/v1/events/{ids['A']}").json()["registration_status"] == "confirmed"   # detail stays text


# --- /search/events (user-facing discovery) and the admin pending queue --------------------------------

@pytest.fixture
def discovery(listing):
    from app.api.v1.endpoints import admin as admin_routes, search as search_routes
    from app.core.dependencies import get_current_super_admin

    sessions, client, ids = listing
    client.app.include_router(search_routes.router, prefix="/api/v1/search")
    client.app.include_router(admin_routes.router, prefix="/api/v1/admin")
    return sessions, client, ids


def test_search_events_marks_the_callers_registrations_in_one_query(discovery):
    from sqlalchemy import event as sa_event

    sessions, client, ids = discovery
    me = {"id": str(uuid4()), "role": "customer", "email": "me@example.com"}
    _register(sessions, ids["A"], me["email"], "confirmed")
    _register(sessions, ids["B"], me["email"], "cancelled")
    _register(sessions, ids["C"], "other@example.com", "confirmed")
    client.app.dependency_overrides[get_optional_current_user] = lambda: me
    engine, statements = sessions.kw["bind"], []
    listener = lambda conn, cur, stmt, *a: statements.append(stmt)
    sa_event.listen(engine, "before_cursor_execute", listener)
    try:
        resp = client.get("/api/v1/search/events", params={"status": "published", "page_size": 50})
    finally:
        sa_event.remove(engine, "before_cursor_execute", listener)

    flags = _flags(resp)
    assert [t for t, v in flags.items() if v] == ["Event A"] and len(flags) == 5
    assert resp.json()["pagination"]["total"] == 5
    assert len([s for s in statements if "participant_email" in s and "event_registrations.event_id IN" in s]) == 1


def test_search_events_anonymous_is_false(discovery):
    sessions, client, ids = discovery
    _register(sessions, ids["A"], "me@example.com", "confirmed")
    assert set(_flags(client.get("/api/v1/search/events")).values()) == {False}


def test_admin_pending_queue_has_no_caller_specific_registration_field(discovery):
    from app.core.dependencies import get_current_super_admin

    sessions, client, ids = discovery
    admin = {"id": str(uuid4()), "role": "super_admin", "email": "root@example.com"}
    with sessions() as db:
        db.add(Event(id=uuid4(), title="Pending one", category="Business", status="pending_approval"))
        db.commit()
    _register(sessions, ids["A"], admin["email"], "confirmed")
    client.app.dependency_overrides[get_current_super_admin] = lambda: admin
    client.app.dependency_overrides[get_optional_current_user] = lambda: admin

    resp = client.get("/api/v1/admin/events/pending")

    assert resp.status_code == 200, resp.text
    [item] = resp.json()["items"]
    assert item["title"] == "Pending one" and "registration_status" not in item
