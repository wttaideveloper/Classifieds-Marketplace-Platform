"""Shared setup for the blog / news tests: an in-memory database, the real routes, the three kinds of caller, and
everything that leaves the process (notifications) captured instead of sent."""
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import blog
from app.core.catalog_access import CatalogAccess, get_catalog_access, get_optional_catalog_user
from app.db.database import Base, get_db
from app.models.blog_model import BlogCategory, BlogPost, BlogPostHistory
from app.models.enterprise_model import Enterprise
from app.models.notification_model import NotificationEventLog
from app.services import blog_notifications

API = "/cms/blogs"
TENANT_A, TENANT_B = uuid4(), uuid4()


def person(name, role, tenant=None):
    return {"id": str(uuid4()), "name": name, "email": f"{name.lower().replace(' ', '.')}@example.test", "role": role,
            "status": "active", "tenant_id": str(tenant) if tenant else None}


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[m.__table__ for m in (BlogCategory, BlogPost, BlogPostHistory, Enterprise, NotificationEventLog)])
    db = sessionmaker(bind=engine)()

    root = person("Platform Root", "super_admin")
    root2 = person("Second Root", "super_admin")
    asha = person("Asha Merchant", "admin", TENANT_A)       # Enterprise Admin of business A
    asha2 = person("Arun Merchant", "admin", TENANT_A)      # another admin of business A
    bela = person("Bela Merchant", "admin", TENANT_B)
    priya = person("Priya Customer", "customer")
    doc = person("Dr Provider", "provider", TENANT_A)
    people = SimpleNamespace(root=root, root2=root2, asha=asha, asha2=asha2, bela=bela, priya=priya, doc=doc)

    db.add(Enterprise(tenant_id=TENANT_A, business_short_name="Calm Clinic", business_legal_name="Calm Clinic Pvt Ltd",
                      business_email="calm@example.test", logo_url="https://cdn.example.test/calm.png"))
    db.add(Enterprise(tenant_id=TENANT_B, business_short_name="Bela Wellness", business_legal_name="Bela Wellness Ltd",
                      business_email="bela@example.test"))
    db.commit()

    notices: list[dict] = []
    monkeypatch.setattr(blog_notifications, "_dispatch", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(
        "app.services.notification_service.create_automatic_notification",
        lambda _db, **kw: notices.append(kw) or SimpleNamespace(id=uuid4()),
    )
    monkeypatch.setattr("app.services.event_notification_service.resolve_platform_admin_user_ids",
                        lambda token=None: [UUIDS(root), UUIDS(root2)])

    @contextmanager
    def session_factory():
        yield db

    monkeypatch.setattr("app.db.database.SessionLocal", session_factory)

    app = FastAPI()
    app.include_router(blog.router, prefix=API)
    state = {"user": None}

    def access():
        user = state["user"]
        if user is None:
            return CatalogAccess("public")
        tenant = user.get("tenant_id")
        return CatalogAccess(user["role"] if user["role"] in ("super_admin", "admin", "customer") else "provider",
                             UUIDS_OPT(tenant) if user["role"] in ("admin", "provider") else None)

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_optional_catalog_user] = lambda: state["user"]
    app.dependency_overrides[get_catalog_access] = access
    client = TestClient(app, raise_server_exceptions=False)

    def as_user(user):
        state["user"] = user
        return client

    w = SimpleNamespace(db=db, client=client, as_user=as_user, people=people, notices=notices, app=app)
    yield w
    db.close()
    engine.dispose()


def UUIDS(user):
    from uuid import UUID
    return UUID(user["id"])


def UUIDS_OPT(value):
    from uuid import UUID
    return UUID(value) if value else None


# ---------------------------------------------------------------- helpers the tests share

def make_category(w, name="Health", **extra):
    resp = w.as_user(w.people.root).post(f"{API}/admin/categories", json={"name": name, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


BODY = "<p>Some <strong>useful</strong> words for readers.</p>"


def draft(w, user, category, **extra):
    body = {"kind": "blog", "title": "Five calm habits", "excerpt": "Small habits.", "content": BODY, "category_id": category["id"], **extra}
    resp = w.as_user(user).post(f"{API}/my/posts", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def submit(w, user, post):
    resp = w.as_user(user).post(f"{API}/my/posts/{post['id']}/submit")
    assert resp.status_code == 200, resp.text
    return resp.json()


def review(w, post, action, reason=None, user=None):
    body = {"action": action, **({"reason": reason} if reason else {})}
    return w.as_user(user or w.people.root).post(f"{API}/admin/posts/{post['id']}/review", json=body)


def live_merchant_post(w, category, user=None, **extra):
    """A merchant post that was submitted and approved, so it is live."""
    post = draft(w, user or w.people.asha, category, **extra)
    submit(w, user or w.people.asha, post)
    resp = review(w, post, "approve")
    assert resp.status_code == 200, resp.text
    return resp.json()


def public_slugs(w, **params):
    resp = w.as_user(None).get(API, params=params)
    assert resp.status_code == 200, resp.text
    return [i["slug"] for i in resp.json()["items"]]


def notices_of(w, category):
    return [n for n in w.notices if n["category"] == category]
