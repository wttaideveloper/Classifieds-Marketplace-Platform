"""Product and service reviews: ownership checks, privacy, cards and the other review rules.

Moderating or deleting someone else's review only works for the business that owns the item (or a Super Admin).
Cards and detail pages show the real approved rating, not the old placeholder 0.
"""
from uuid import uuid4

import pytest
from fastapi import FastAPI
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
from app.services import review_notifications

TENANT, OTHER = uuid4(), uuid4()
BUYER = {"id": str(uuid4()), "email": "buyer@example.com", "role": "customer", "name": "Asha Rao"}


def as_user(client, user):
    client.app.dependency_overrides[get_current_user] = lambda: user
    client.app.dependency_overrides[get_optional_catalog_user] = lambda: user


def staff(role="admin", tenant=TENANT):
    return {"id": str(uuid4()), "email": f"{role}@example.com", "role": role, "tenant_id": str(tenant)}


@pytest.fixture
def dispatched(monkeypatch):
    calls = []
    monkeypatch.setattr(review_notifications, "_dispatch", lambda fn, *a, **k: calls.append((fn.__name__, a)))
    return calls


@pytest.fixture
def world(monkeypatch, dispatched):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[
        Enterprise.__table__, Product.__table__, ProductReview.__table__, Service.__table__, ServiceReview.__table__,
        Order.__table__, OrderItem.__table__,
    ])
    sessions = sessionmaker(bind=engine)
    ids = {"ent": uuid4(), "product": uuid4(), "service": uuid4()}
    with sessions() as db:
        db.add(Enterprise(id=ids["ent"], tenant_id=TENANT, business_short_name="Acme", business_legal_name="Acme Ltd", business_email="a@a.com"))
        db.add(Product(id=ids["product"], enterprise_id=ids["ent"], product_name="Yoga Mat", product_category="Fitness", product_price=49.99))
        db.add(Service(id=ids["service"], enterprise_id=ids["ent"], service_name="Massage", service_category="Wellness", duration=60, service_price=100.0))
        db.commit()

    app = FastAPI()
    app.include_router(product_routes.router, prefix="/api/v1/products")
    app.include_router(service_routes.router, prefix="/api/v1/services")

    def database():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        as_user(client, BUYER)
        yield client, sessions, ids
    engine.dispose()


KINDS = [("products", "product"), ("services", "service")]


def post(client, kind, item_id, rating=5, comment="Great"):
    return client.post(f"/api/v1/{kind}/{item_id}/reviews", json={"rating": rating, "comment": comment})


def act(client, user, method, url, **kw):
    as_user(client, user)
    try:
        return getattr(client, method)(url, **kw)
    finally:
        as_user(client, BUYER)


@pytest.mark.parametrize("kind,item", KINDS)
def test_only_the_owning_business_or_a_super_admin_can_moderate(world, kind, item):
    client, _, ids = world
    iid = ids[item]
    review_id = post(client, kind, iid).json()["id"]
    url = f"/api/v1/{kind}/{iid}/reviews/{review_id}/moderate"

    for user in (staff("admin", OTHER), staff("provider"), BUYER):
        assert act(client, user, "patch", url, json={"action": "approved"}).status_code == 403, user
    assert client.get(f"/api/v1/{kind}/{iid}/reviews").json()["total"] == 0

    assert act(client, staff("admin"), "patch", url, json={"action": "approved"}).status_code == 200
    root = {"id": str(uuid4()), "role": "super_admin", "email": "root@example.com"}
    assert act(client, root, "patch", url, json={"action": "rejected"}).status_code == 200


@pytest.mark.parametrize("kind,item", KINDS)
def test_moderate_checks_the_review_is_on_the_item_in_the_url(world, kind, item):
    client, sessions, ids = world
    review_id = post(client, kind, ids[item]).json()["id"]
    # a second item of the same business: the review is not on it
    with sessions() as db:
        other_id = uuid4()
        if kind == "products":
            db.add(Product(id=other_id, enterprise_id=ids["ent"], product_name="Block", product_category="Fitness", product_price=5))
        else:
            db.add(Service(id=other_id, enterprise_id=ids["ent"], service_name="Reiki", service_category="Wellness", duration=30, service_price=10.0))
        db.commit()

    wrong = act(client, staff(), "patch", f"/api/v1/{kind}/{other_id}/reviews/{review_id}/moderate", json={"action": "approved"})

    assert wrong.status_code == 404


@pytest.mark.parametrize("kind,item", KINDS)
def test_staff_can_only_delete_reviews_of_their_own_business(world, kind, item):
    client, sessions, ids = world
    iid = ids[item]
    review_id = post(client, kind, iid).json()["id"]
    url = f"/api/v1/{kind}/{iid}/reviews/{review_id}"

    assert act(client, staff("admin", OTHER), "delete", url).status_code == 403
    assert act(client, staff("provider", OTHER), "delete", url).status_code == 403
    assert act(client, staff("provider"), "delete", url).status_code == 200


@pytest.mark.parametrize("kind,item", KINDS)
def test_a_super_admin_can_delete_any_review(world, kind, item):
    client, _, ids = world
    iid = ids[item]
    review_id = post(client, kind, iid).json()["id"]
    root = {"id": str(uuid4()), "role": "super_admin", "email": "root@example.com"}

    assert act(client, root, "delete", f"/api/v1/{kind}/{iid}/reviews/{review_id}").status_code == 200


@pytest.mark.parametrize("kind,item", KINDS)
def test_the_reviewer_name_is_never_an_email(world, kind, item):
    client, sessions, ids = world
    iid = ids[item]
    nameless = {"id": str(uuid4()), "email": "nameless@example.com", "role": "customer"}
    as_user(client, nameless)
    saved = post(client, kind, iid).json()
    as_user(client, BUYER)
    assert saved["reviewer_name"] is None

    # a review saved before this rule, with an email as its "name", is not shown with it either
    Model = ProductReview if kind == "products" else ServiceReview
    fk = "product_id" if kind == "products" else "service_id"
    with sessions() as db:
        db.add(Model(**{fk: iid}, user_id=uuid4(), reviewer_name="old@example.com", rating="4", moderation_status="approved"))
        db.commit()
    shown = client.get(f"/api/v1/{kind}/{iid}/reviews").json()["reviews"]
    assert shown[0]["reviewer_name"] is None and "old@example.com" not in str(shown)


@pytest.mark.parametrize("kind,item", KINDS)
def test_the_list_has_the_star_breakdown_and_a_null_average_when_empty(world, kind, item):
    client, sessions, ids = world
    iid = ids[item]
    empty = client.get(f"/api/v1/{kind}/{iid}/reviews").json()
    assert empty["average_rating"] is None and empty["total"] == 0
    assert empty["rating_distribution"] == {"5": 0, "4": 0, "3": 0, "2": 0, "1": 0}

    Model = ProductReview if kind == "products" else ServiceReview
    fk = "product_id" if kind == "products" else "service_id"
    with sessions() as db:
        for rating in ("5", "5", "4"):
            db.add(Model(**{fk: iid}, user_id=uuid4(), reviewer_name="N", rating=rating, moderation_status="approved"))
        db.add(Model(**{fk: iid}, user_id=uuid4(), reviewer_name="N", rating="1", moderation_status="pending"))
        db.commit()
    full = client.get(f"/api/v1/{kind}/{iid}/reviews").json()
    assert full["total"] == 3 and full["average_rating"] == 4.67
    assert full["rating_distribution"] == {"5": 2, "4": 1, "3": 0, "2": 0, "1": 0}


@pytest.mark.parametrize("kind,item", KINDS)
def test_my_review_is_null_until_written_then_shows_the_status(world, kind, item):
    client, _, ids = world
    iid = ids[item]
    assert client.get(f"/api/v1/{kind}/{iid}/reviews/me").json() is None

    post(client, kind, iid, rating=3)
    mine = client.get(f"/api/v1/{kind}/{iid}/reviews/me").json()

    assert mine["rating"] == 3 and mine["moderation_status"] == "pending"


@pytest.mark.parametrize("kind,item", KINDS)
def test_comment_limit_and_edit_keeps_status(world, kind, item):
    client, _, ids = world
    iid = ids[item]
    assert post(client, kind, iid, comment="x" * 2001).status_code == 422
    review_id = post(client, kind, iid, comment="x" * 2000).json()["id"]
    act(client, staff(), "patch", f"/api/v1/{kind}/{iid}/reviews/{review_id}/moderate", json={"action": "approved"})

    edited = post(client, kind, iid, rating=2, comment="  changed  ")

    assert edited.json()["id"] == review_id and edited.json()["comment"] == "changed"
    assert edited.json()["moderation_status"] == "approved"


# --- cards and detail pages ----------------------------------------------------------------------

def approve_reviews(sessions, kind, iid, ratings):
    Model = ProductReview if kind == "products" else ServiceReview
    fk = "product_id" if kind == "products" else "service_id"
    with sessions() as db:
        for rating, status in ratings:
            db.add(Model(**{fk: iid}, user_id=uuid4(), reviewer_name="Ravi", rating=str(rating), comment=f"{rating} stars",
                         moderation_status=status))
        db.commit()


@pytest.mark.parametrize("kind,item", KINDS)
def test_cards_show_the_real_approved_rating_and_count(world, kind, item):
    client, sessions, ids = world
    iid = ids[item]
    card = client.get(f"/api/v1/{kind}/").json()["items"][0]
    assert card["rating"] == 0 and card["reviews_count"] == 0

    approve_reviews(sessions, kind, iid, [(5, "approved"), (4, "approved"), (1, "pending"), (1, "rejected")])
    card = client.get(f"/api/v1/{kind}/").json()["items"][0]

    assert card["rating"] == 4.5 and card["reviews_count"] == 2


@pytest.mark.parametrize("kind,item", KINDS)
def test_the_detail_page_shows_the_real_approved_reviews(world, kind, item):
    client, sessions, ids = world
    iid = ids[item]
    approve_reviews(sessions, kind, iid, [(5, "approved"), (2, "pending")])

    detail = client.get(f"/api/v1/{kind}/{iid}").json()

    assert detail["rating"] == 5.0 and detail["reviews_count"] == 1
    assert [r["comment"] for r in detail["reviews"]] == ["5 stars"]
    assert detail["reviews"][0]["reviewer_name"] == "Ravi"


# --- notifications -------------------------------------------------------------------------------

@pytest.mark.parametrize("kind,item", KINDS)
def test_notifications_on_submit_and_on_decisions_only(world, dispatched, kind, item):
    client, _, ids = world
    iid = ids[item]
    review_id = post(client, kind, iid).json()["id"]
    post(client, kind, iid, rating=1)  # an edit: not announced again
    assert [(n, a[0]) for n, a in dispatched] == [("_deliver_submitted", item)]
    dispatched.clear()

    url = f"/api/v1/{kind}/{iid}/reviews/{review_id}/moderate"
    for action in ("approved", "approved", "pending", "rejected"):
        act(client, staff(), "patch", url, json={"action": action})

    assert [(n, a[2]) for n, a in dispatched] == [("_deliver_decision", "approved"), ("_deliver_decision", "rejected")]


# --- two first posts at the same moment -----------------------------------------------------------

@pytest.mark.parametrize("kind,item,Model", [("products", "product", ProductReview), ("services", "service", ServiceReview)])
def test_simultaneous_first_posts_update_the_winner_instead_of_failing(world, monkeypatch, kind, item, Model):
    from sqlalchemy.orm import Query

    client, sessions, ids = world
    iid = ids[item]
    first = post(client, kind, iid, rating=5, comment="the other request won")
    assert first.status_code == 201

    # This request looked for an existing review before the other one saved it: its first lookup finds nothing,
    # so it tries to insert and runs into the unique index.
    original, hidden = Query.first, {"done": False}

    def first_lookup_misses(self):
        entity = self.column_descriptions[0].get("entity")
        if entity is Model and not hidden["done"]:
            hidden["done"] = True
            return None
        return original(self)

    monkeypatch.setattr(Query, "first", first_lookup_misses)
    second = post(client, kind, iid, rating=2, comment="second")

    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"] and second.json()["rating"] == 2
    with sessions() as db:
        assert db.query(Model).count() == 1


# --- search results carry the same rating --------------------------------------------------------

@pytest.mark.parametrize("kind,item", KINDS)
def test_search_results_show_the_real_approved_rating(world, kind, item):
    from app.services import search_service

    client, sessions, ids = world
    iid = ids[item]
    approve_reviews(sessions, kind, iid, [(5, "approved"), (3, "approved"), (1, "pending")])
    search = search_service.search_products_service if kind == "products" else search_service.search_services_service

    with sessions() as db:
        [card] = search(db).items

    assert card.rating == 4.0 and card.reviews_count == 2


# --- sorting, filtering and paging the public list ------------------------------------------------

def seed_ordered(sessions, kind, iid):
    """Five approved reviews, oldest first: 5*, 3*, 4*, 3*, 1* (the 3* and the 5* have comments)."""
    from datetime import datetime, timedelta

    Model = ProductReview if kind == "products" else ServiceReview
    fk = "product_id" if kind == "products" else "service_id"
    with sessions() as db:
        for minute, (rating, comment) in enumerate([(5, "five"), (3, "three-a"), (4, None), (3, "three-b"), (1, None)]):
            db.add(Model(**{fk: iid}, user_id=uuid4(), reviewer_name="N", rating=str(rating), comment=comment,
                         moderation_status="approved", created_at=datetime(2026, 1, 1, 0, minute)))
        db.commit()


def ratings_of(client, kind, iid, **params):
    body = client.get(f"/api/v1/{kind}/{iid}/reviews", params=params).json()
    return [r["rating"] for r in body["reviews"]], body


@pytest.mark.parametrize("kind,item", KINDS)
def test_sorting(world, kind, item):
    client, sessions, ids = world
    iid = ids[item]
    seed_ordered(sessions, kind, iid)

    assert ratings_of(client, kind, iid)[0] == [1, 3, 4, 3, 5]                      # newest first (default)
    assert ratings_of(client, kind, iid, sort="oldest")[0] == [5, 3, 4, 3, 1]
    assert ratings_of(client, kind, iid, sort="highest")[0] == [5, 4, 3, 3, 1]      # ties: newest first
    assert ratings_of(client, kind, iid, sort="lowest")[0] == [1, 3, 3, 4, 5]
    assert client.get(f"/api/v1/{kind}/{iid}/reviews", params={"sort": "best"}).status_code == 422


@pytest.mark.parametrize("kind,item", KINDS)
def test_filters_do_not_change_the_summary(world, kind, item):
    client, sessions, ids = world
    iid = ids[item]
    seed_ordered(sessions, kind, iid)

    ratings, body = ratings_of(client, kind, iid, rating=3)
    assert ratings == [3, 3] and body["pagination"]["total"] == 2
    assert body["total"] == 5 and body["average_rating"] == 3.2     # the summary still covers all five
    assert body["rating_distribution"] == {"5": 1, "4": 1, "3": 2, "2": 0, "1": 1}

    ratings, body = ratings_of(client, kind, iid, with_comment="true")
    assert ratings == [3, 3, 5] and body["pagination"]["total"] == 3
    assert ratings_of(client, kind, iid, rating=2)[0] == []
    assert client.get(f"/api/v1/{kind}/{iid}/reviews", params={"rating": 6}).status_code == 422


@pytest.mark.parametrize("kind,item", KINDS)
def test_paging(world, kind, item):
    client, sessions, ids = world
    iid = ids[item]
    seed_ordered(sessions, kind, iid)

    everything = ratings_of(client, kind, iid)[1]["pagination"]
    assert everything == {"total": 5, "page": 1, "page_size": 5, "total_pages": 1}   # no paging asked: all of them

    first, body = ratings_of(client, kind, iid, page=1, page_size=2)
    assert first == [1, 3] and body["pagination"] == {"total": 5, "page": 1, "page_size": 2, "total_pages": 3}
    assert ratings_of(client, kind, iid, page=3, page_size=2)[0] == [5]
    assert ratings_of(client, kind, iid, page=9, page_size=2)[0] == []
    assert ratings_of(client, kind, iid, page=2)[1]["pagination"]["page_size"] == 20     # page alone: 20 per page
    assert body["total"] == 5 and body["average_rating"] == 3.2
    assert client.get(f"/api/v1/{kind}/{iid}/reviews", params={"page_size": 101}).status_code == 422
    assert client.get(f"/api/v1/{kind}/{iid}/reviews", params={"page": 0}).status_code == 422
