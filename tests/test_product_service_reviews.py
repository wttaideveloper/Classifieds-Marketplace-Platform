"""Product/Service reviews: submit (upsert), verified-purchase detection,
public list scoped to approved-only, delete, and moderation."""
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import product as product_routes
from app.api.v1.endpoints import service as service_routes
from app.core.catalog_access import get_optional_catalog_user
from app.core.dependencies import get_current_user
from app.db.database import Base, get_db
from app.models.cart_model import Order, OrderItem
from app.models.enterprise_model import Enterprise
from app.models.product_model import Product, ProductReview
from app.models.service_model import Service, ServiceReview


def _set_identity(app, identity_or_raiser):
    """require_catalog_writer's chain calls get_current_user() as a plain
    function rather than via Depends(...), so overriding get_current_user
    alone doesn't reach it — get_optional_catalog_user must be overridden too."""
    app.dependency_overrides[get_current_user] = identity_or_raiser
    app.dependency_overrides[get_optional_catalog_user] = identity_or_raiser


@pytest.fixture
def reviews_app(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[
        Enterprise.__table__, Product.__table__, ProductReview.__table__,
        Service.__table__, ServiceReview.__table__, Order.__table__, OrderItem.__table__,
    ])
    sessions = sessionmaker(bind=engine)
    ent_id, pid, sid = uuid4(), uuid4(), uuid4()
    buyer_id = uuid4()
    with sessions() as db:
        db.add(Enterprise(id=ent_id, business_short_name="Acme", business_legal_name="Acme Ltd", business_email="a@a.com"))
        db.add(Product(id=pid, enterprise_id=ent_id, product_name="Yoga Mat", product_category="Fitness", product_price=49.99))
        db.add(Service(id=sid, enterprise_id=ent_id, service_name="Massage", service_category="Wellness", duration=60, service_price=100.0))
        db.commit()
        order = Order(id=uuid4(), user_id=buyer_id, status="confirmed", total=49.99)
        db.add(order); db.commit()
        db.add(OrderItem(id=uuid4(), order_id=order.id, product_id=pid, product_name="Yoga Mat", quantity=1, unit_price=49.99, line_total=49.99))
        db.commit()

    user = {"id": str(buyer_id), "email": "buyer@example.com", "role": "customer"}
    app = FastAPI()
    app.include_router(product_routes.router, prefix="/api/v1/products")
    app.include_router(service_routes.router, prefix="/api/v1/services")

    def database():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = database
    _set_identity(app, lambda: user)
    with TestClient(app) as client:
        yield sessions, client, user, pid, sid
    engine.dispose()


def test_product_review_verified_purchase_and_moderation_gate(reviews_app):
    sessions, client, user, pid, _ = reviews_app
    response = client.post(f"/api/v1/products/{pid}/reviews", json={"rating": 5, "comment": "Great mat!"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["is_verified_purchase"] is True
    assert body["moderation_status"] == "pending"

    # not yet approved -> hidden from the public list
    listing = client.get(f"/api/v1/products/{pid}/reviews").json()
    assert listing["total"] == 0
    assert listing["reviews"] == []
    assert listing["average_rating"] is None


def test_product_review_unverified_for_non_buyer(reviews_app):
    sessions, client, user, pid, _ = reviews_app
    stranger_id = uuid4()
    app = client.app
    _set_identity(app, lambda: {"id": str(stranger_id), "email": "stranger@example.com", "role": "customer"})
    response = client.post(f"/api/v1/products/{pid}/reviews", json={"rating": 2})
    assert response.status_code == 201
    assert response.json()["is_verified_purchase"] is False


def test_product_review_upsert_no_duplicates(reviews_app):
    sessions, client, user, pid, _ = reviews_app
    first = client.post(f"/api/v1/products/{pid}/reviews", json={"rating": 5, "comment": "v1"}).json()
    second = client.post(f"/api/v1/products/{pid}/reviews", json={"rating": 3, "comment": "v2"}).json()
    assert first["id"] == second["id"]
    assert second["rating"] == 3
    with sessions() as db:
        assert db.query(ProductReview).filter(ProductReview.product_id == pid).count() == 1


def test_product_review_moderation_flow(reviews_app):
    sessions, client, user, pid, _ = reviews_app
    review = client.post(f"/api/v1/products/{pid}/reviews", json={"rating": 5}).json()
    review_id = review["id"]

    staff = {"id": str(uuid4()), "email": "admin@example.com", "role": "admin", "tenant_id": str(uuid4())}
    _set_identity(client.app, lambda: staff)

    moderated = client.patch(f"/api/v1/products/{pid}/reviews/{review_id}/moderate", json={"action": "approved"})
    assert moderated.status_code == 200, moderated.text
    assert moderated.json()["moderation_status"] == "approved"

    listing = client.get(f"/api/v1/products/{pid}/reviews").json()
    assert listing["total"] == 1
    assert listing["average_rating"] == 5.0

    client.patch(f"/api/v1/products/{pid}/reviews/{review_id}/moderate", json={"action": "rejected"})
    listing = client.get(f"/api/v1/products/{pid}/reviews").json()
    assert listing["total"] == 0


def test_product_review_delete_owner_vs_stranger(reviews_app):
    sessions, client, user, pid, _ = reviews_app
    review = client.post(f"/api/v1/products/{pid}/reviews", json={"rating": 4}).json()
    review_id = review["id"]

    app = client.app
    stranger = {"id": str(uuid4()), "email": "stranger@example.com", "role": "customer"}
    _set_identity(app, lambda: stranger)
    forbidden = client.delete(f"/api/v1/products/{pid}/reviews/{review_id}")
    assert forbidden.status_code == 403

    _set_identity(app, lambda: user)
    ok = client.delete(f"/api/v1/products/{pid}/reviews/{review_id}")
    assert ok.status_code == 200
    with sessions() as db:
        assert db.query(ProductReview).filter(ProductReview.id == UUID(review_id)).first() is None


def test_product_review_staff_can_delete_others_review(reviews_app):
    sessions, client, user, pid, _ = reviews_app
    review = client.post(f"/api/v1/products/{pid}/reviews", json={"rating": 4}).json()
    app = client.app
    staff = {"id": str(uuid4()), "email": "admin@example.com", "role": "admin", "tenant_id": str(uuid4())}
    _set_identity(app, lambda: staff)
    ok = client.delete(f"/api/v1/products/{pid}/reviews/{review['id']}")
    assert ok.status_code == 200


def test_product_review_requires_authentication(reviews_app):
    sessions, client, user, pid, _ = reviews_app
    app = client.app

    def raise_401():
        raise HTTPException(status_code=401, detail="Not authenticated")
    app.dependency_overrides[get_current_user] = raise_401
    response = client.post(f"/api/v1/products/{pid}/reviews", json={"rating": 5})
    assert response.status_code == 401


def test_service_review_is_never_verified_and_still_works(reviews_app):
    sessions, client, user, _, sid = reviews_app
    response = client.post(f"/api/v1/services/{sid}/reviews", json={"rating": 4, "comment": "Nice"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["is_verified_purchase"] is False

    staff = {"id": str(uuid4()), "email": "admin@example.com", "role": "admin", "tenant_id": str(uuid4())}
    _set_identity(client.app, lambda: staff)
    moderated = client.patch(f"/api/v1/services/{sid}/reviews/{body['id']}/moderate", json={"action": "approved"})
    assert moderated.status_code == 200, moderated.text
    listing = client.get(f"/api/v1/services/{sid}/reviews").json()
    assert listing["total"] == 1
    assert listing["reviews"][0]["rating"] == 4


def test_review_rejects_out_of_range_rating(reviews_app):
    sessions, client, user, pid, _ = reviews_app
    response = client.post(f"/api/v1/products/{pid}/reviews", json={"rating": 6})
    assert response.status_code == 422


def test_review_404s_for_unknown_product(reviews_app):
    sessions, client, user, pid, _ = reviews_app
    response = client.post(f"/api/v1/products/{uuid4()}/reviews", json={"rating": 5})
    assert response.status_code == 404
