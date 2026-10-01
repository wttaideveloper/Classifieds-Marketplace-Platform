"""Real-SQL harness for Event Management tests (in-memory SQLite, no network).

The Event models use Postgres ``JSONB``/``UUID``. SQLite renders ``UUID`` natively as CHAR(32) and,
with the shim below, ``JSONB`` as JSON — so tenant-isolation and capacity tests run the *actual*
SQL the endpoints emit instead of asserting against canned ``MagicMock`` query chains.

The session mirrors production: ``autocommit=False, autoflush=False``.

Helpers are not tests (no ``test_`` prefix) and are imported by the Phase 2.1+ test modules.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy import event as sa_event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import get_current_user, get_optional_current_user
from app.db.database import Base, get_db
from app.main import app as fastapi_app
import app.models as _models  # noqa: F401  (registers every model on Base.metadata)
from app.models.enterprise_model import Enterprise
from app.models.event_aux_models import (
    EventAudit,
    EventCategory,
    EventFeedback,
    EventOrder,
    EventRegistration,
    EventRegistrationOption,
    EventSessionAttendance,
    EventTemplate,
    EventWaitlist,
)
from app.models.event_form_config_model import EventFormConfiguration, EventFormConfigurationVersion
from app.models.event_model import Event
from app.models.event_type_model import EventTypeConfig


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):  # pragma: no cover - trivial shim
    return "JSON"


_TABLES = [
    Enterprise.__table__,
    Event.__table__,
    EventRegistration.__table__,
    EventRegistrationOption.__table__,
    EventSessionAttendance.__table__,
    EventOrder.__table__,
    EventWaitlist.__table__,
    EventTemplate.__table__,
    EventFeedback.__table__,
    EventAudit.__table__,
    EventCategory.__table__,
    EventFormConfiguration.__table__,
    EventFormConfigurationVersion.__table__,
    EventTypeConfig.__table__,
]

# The 7 Event Types seeded by the Phase 2.2 migration (070237a7c1cf), written out independently here —
# same "typed out independently of the implementation" convention as SPEC_DEFAULTS in
# tests/test_event_modules_config.py, so a drift between the two is a real, caught test failure rather
# than a passing test that merely imports whatever the implementation currently says.
_ALL_ALLOWED = {k: True for k in (
    "registration", "tickets", "sessions", "check_in", "online_meeting", "custom_questions", "meals", "accommodation")}
_NONE_REQUIRED = {k: False for k in _ALL_ALLOWED}
SEEDED_EVENT_TYPE_DEFAULTS = {
    "conference": {**_NONE_REQUIRED, "registration": True, "tickets": True, "sessions": True, "check_in": True, "meals": True, "accommodation": True},
    "workshop": {**_NONE_REQUIRED, "registration": True, "tickets": True, "sessions": True, "check_in": True},
    "marathon": {**_NONE_REQUIRED, "registration": True, "tickets": True, "check_in": True},
    "camp": {**_NONE_REQUIRED, "registration": True, "check_in": True, "meals": True, "accommodation": True},
    "private_function": {**_NONE_REQUIRED, "registration": True, "check_in": True, "custom_questions": True, "meals": True},
    "webinar": {**_NONE_REQUIRED, "registration": True, "sessions": True, "online_meeting": True},
    "other": {**_NONE_REQUIRED, "registration": True},
}
SEEDED_EVENT_TYPE_NAMES = {
    "conference": "Conference", "workshop": "Workshop", "marathon": "Marathon", "camp": "Camp",
    "private_function": "Private Function", "webinar": "Webinar", "other": "Other",
}


def seed_event_types(db):
    """The 7 Phase 2.2 Event Types, exactly as the real migration seeds them (fully-allowed,
    nothing-required) — called once per test session so `event_type="conference"` etc. keeps resolving
    against a real, active EventTypeConfig row without every test needing its own setup."""
    for key, defaults in SEEDED_EVENT_TYPE_DEFAULTS.items():
        db.add(EventTypeConfig(
            key=key, name=SEEDED_EVENT_TYPE_NAMES[key], active=True,
            default_modules=defaults, allowed_modules=dict(_ALL_ALLOWED), required_modules=dict(_NONE_REQUIRED),
        ))
    db.commit()


def make_session():
    """A fresh in-memory database + session (autoflush off, like production)."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    # pysqlite needs this for SAVEPOINT (begin_nested) to behave, per the SQLAlchemy docs.
    @sa_event.listens_for(engine, "connect")
    def _no_implicit_begin(dbapi_connection, _record):
        dbapi_connection.isolation_level = None

    @sa_event.listens_for(engine, "begin")
    def _explicit_begin(conn):
        conn.exec_driver_sql("BEGIN")

    Base.metadata.create_all(engine, tables=_TABLES)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    seed_event_types(session)
    return session


# ---------------------------------------------------------------------------- builders

def make_enterprise(db, tenant_id, name="Enterprise"):
    ent = Enterprise(
        tenant_id=tenant_id,
        business_short_name=name,
        business_legal_name=f"{name} Ltd",
        business_email=f"{uuid4().hex[:8]}@example.com",
        status="approved",
    )
    db.add(ent)
    db.commit()
    return ent


def make_event(db, tenant_id, enterprise=None, **overrides):
    now = datetime.utcnow()
    values = dict(
        tenant_id=tenant_id,
        enterprise_id=enterprise.id if enterprise is not None else None,
        title="Original title",
        description="An event",
        category="Wellness",
        organiser_name="Org",
        organiser_contact="organiser@example.com",
        start_date=now + timedelta(days=10),
        end_date=now + timedelta(days=11),
        time_zone="UTC",
        delivery_mode="in_person",
        pricing_type="free",
        ticket_types=[],
        sessions=[],
        status="published",
        last_admin_notes="internal reviewer note",
    )
    values.update(overrides)
    event = Event(**values)
    db.add(event)
    db.commit()
    return event


def make_registration(db, event, email, status="confirmed", ticket_type_id=None, created_at=None, **extra):
    reg = EventRegistration(
        event_id=event.id,
        participant_name=email.split("@")[0],
        participant_email=email.strip().lower(),
        ticket_type_id=ticket_type_id,
        status=status,
        qr_code=uuid4().hex[:12].upper(),
        created_at=created_at or datetime.utcnow(),
        **extra,
    )
    db.add(reg)
    db.commit()
    return reg


def make_order(db, event, email, ticket_type_id=None, quantity="1", amount="100", status="confirmed",
               payment_status="confirmed", created_at=None):
    order = EventOrder(
        event_id=event.id,
        participant_name=email.split("@")[0],
        participant_email=email.strip().lower(),
        ticket_type_id=ticket_type_id,
        quantity=quantity,
        amount=amount,
        currency="INR",
        status=status,
        payment_status=payment_status,
        created_at=created_at or datetime.utcnow(),
    )
    db.add(order)
    db.commit()
    return order


def make_waitlist(db, event, email, status="waiting", created_at=None, expires_at=None):
    entry = EventWaitlist(
        event_id=event.id,
        participant_name=email.split("@")[0],
        participant_email=email.strip().lower(),
        status=status,
        payment_offer_expires_at=expires_at,
        created_at=created_at or datetime.utcnow(),
    )
    db.add(entry)
    db.commit()
    return entry


def paid_ticket(price="500", capacity=None, ticket_id=None):
    return {"id": ticket_id or str(uuid4()), "name": "General", "price": price, "currency": "INR", "capacity": capacity}


# ---------------------------------------------------------------------------- identities

def staff_user(tenant_id, role="admin", email=None):
    return {
        "id": str(uuid4()),
        "role": role,
        "email": email or f"{role}-{uuid4().hex[:6]}@example.com",
        "tenant_id": str(tenant_id),
    }


def customer_user(email):
    return {"id": str(uuid4()), "role": "customer", "email": email}


def super_admin_user():
    return {"id": str(uuid4()), "role": "super_admin", "email": "root@example.com", "isSuperAdmin": True, "status": "active"}


# ---------------------------------------------------------------------------- client

def _raise_unauthenticated():
    raise HTTPException(status_code=401, detail="Not authenticated")


def client_for(db, user=None) -> TestClient:
    """TestClient bound to ``db`` and authenticated as ``user`` (None = anonymous).

    Both the strict (``get_current_user``) and optional (``get_optional_current_user``) auth
    dependencies are overridden so tests never depend on the environment's dev-token settings.
    Call :func:`reset_overrides` in teardown.
    """
    fastapi_app.dependency_overrides[get_db] = lambda: db
    if user is None:
        fastapi_app.dependency_overrides[get_current_user] = _raise_unauthenticated
        fastapi_app.dependency_overrides[get_optional_current_user] = lambda: None
    else:
        fastapi_app.dependency_overrides[get_current_user] = lambda: user
        fastapi_app.dependency_overrides[get_optional_current_user] = lambda: user
    return TestClient(fastapi_app, raise_server_exceptions=False)


def reset_overrides():
    fastapi_app.dependency_overrides.clear()


def silence_side_effects(monkeypatch):
    """No notifications / broker traffic from unit tests. Returns the mocked Celery ``apply_async``."""
    monkeypatch.setattr("app.services.notification_triggers._safe_notify", lambda *a, **k: None)
    # Identity-service lookups (tenant/me, auth/me, internal user) must never leave the process.
    for target in (
        "app.services.super_admin_identity.fetch_internal_user_by_id",
        "app.services.super_admin_identity.fetch_auth_me_profile",
        "app.services.invigorate_auth_client.fetch_tenant_me_profile",
        "app.services.invigorate_auth_client.fetch_auth_me_profile",
    ):
        monkeypatch.setattr(target, lambda *a, **k: None)
    apply_async = MagicMock()
    monkeypatch.setattr("app.tasks.event_tasks.expire_waitlist_offer_task.apply_async", apply_async)
    return apply_async


API = "/api/v1/events"
