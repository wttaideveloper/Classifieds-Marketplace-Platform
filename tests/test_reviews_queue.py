"""GET /reviews/manage and PATCH /reviews/{module}/{review_id}/moderate: one moderation queue across
training/course, product, service and event reviews, scoped to the caller's business."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import review_admin
from app.core.catalog_access import get_optional_catalog_user
from app.core.dependencies import get_current_user
from app.db.database import Base, get_db
from app.models.cart_model import Order, OrderItem
from app.models.event_aux_models import EventFeedback
from app.models.product_model import Product, ProductReview
from app.models.service_model import Service, ServiceReview
from app.models.training_model import (
    Training, TrainingAssessmentSubmission, TrainingAssignmentSubmission, TrainingEnrolment, TrainingLessonAttendance,
    TrainingLiveSession, TrainingOrder, TrainingProgress, TrainingReview, TrainingWaitlist,
)
from app.services import review_notifications
from tests.event_sql_support import make_enterprise, make_event, make_registration, make_session

A, B = uuid4(), uuid4()          # two businesses
NOW = datetime(2026, 10, 1, 12, 0, 0)


def staff(role="admin", tenant=A):
    return {"id": str(uuid4()), "email": f"{role}@example.com", "role": role, "tenant_id": str(tenant)}


ROOT = {"id": str(uuid4()), "email": "root@example.com", "role": "super_admin"}
CUSTOMER = {"id": str(uuid4()), "email": "c@example.com", "role": "customer"}


@pytest.fixture
def dispatched(monkeypatch):
    calls = []
    monkeypatch.setattr(review_notifications, "_dispatch", lambda fn, *a, **k: calls.append((fn.__name__, a)))
    return calls


def seed(db):
    """Business A: one item of each kind with one review each (T+0..T+3 minutes). Business B: the same, one review."""
    Base.metadata.create_all(db.bind, tables=[m.__table__ for m in (
        Training, TrainingEnrolment, TrainingProgress, TrainingAssessmentSubmission, TrainingAssignmentSubmission,
        TrainingLessonAttendance, TrainingReview, TrainingOrder, TrainingLiveSession, TrainingWaitlist,
        Product, ProductReview, Service, ServiceReview, Order, OrderItem,
    )])
    ids = SimpleNamespace()
    for tag, tenant in (("a", A), ("b", B)):
        ent = make_enterprise(db, tenant, name=f"Business {tag.upper()}")
        t = Training(id=uuid4(), enterprise_id=ent.id, tenant_id=tenant, title=f"Training {tag}", category="x", status="published",
                     delivery_mode="online", sections=[], assessments=[], assignments=[])
        p = Product(id=uuid4(), enterprise_id=ent.id, tenant_id=tenant, product_name=f"Product {tag}", product_category="x", product_price=5)
        s = Service(id=uuid4(), enterprise_id=ent.id, tenant_id=tenant, service_name=f"Service {tag}", service_category="x", duration=30, service_price=10.0)
        e = make_event(db, tenant, enterprise=ent, title=f"Event {tag}")
        db.add_all([t, p, s])
        db.add(TrainingEnrolment(training_id=t.id, participant_name=f"Learner {tag}", participant_email=f"l-{tag}@example.com", status="enrolled"))
        db.commit()
        make_registration(db, e, f"e-{tag}@example.com")
        offset = 0 if tag == "a" else 10
        db.add_all([
            TrainingReview(training_id=t.id, participant_email=f"l-{tag}@example.com", rating="5", comment="great course", moderation_status="pending", created_at=NOW + timedelta(minutes=offset)),
            ProductReview(product_id=p.id, user_id=uuid4(), reviewer_name="Ravi", rating="4", comment="solid product", is_verified_purchase=True, moderation_status="approved", created_at=NOW + timedelta(minutes=offset + 1)),
            ServiceReview(service_id=s.id, user_id=uuid4(), reviewer_name="old@example.com", rating="3", comment="fine service", moderation_status="rejected", created_at=NOW + timedelta(minutes=offset + 2)),
            EventFeedback(event_id=e.id, participant_email=f"e-{tag}@example.com", rating="2", comment="meh event", is_review=True, moderation_status="pending", created_at=NOW + timedelta(minutes=offset + 3)),
        ])
        db.commit()
        setattr(ids, tag, SimpleNamespace(training=t.id, product=p.id, service=s.id, event=e.id))
    return ids


@pytest.fixture
def world(dispatched):
    db = make_session()
    ids = seed(db)
    app = FastAPI()
    app.include_router(review_admin.router, prefix="/api/v1/reviews")
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app, raise_server_exceptions=False)

    def as_user(user):
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_optional_catalog_user] = lambda: user
        return client

    as_user(staff())
    yield SimpleNamespace(client=client, as_user=as_user, db=db, ids=ids)
    db.close()


def queue(world, user=None, **params):
    if user is not None:
        world.as_user(user)
    return world.client.get("/api/v1/reviews/manage", params=params)


# --- scope ---------------------------------------------------------------------------------------

def test_an_admin_sees_every_module_of_their_own_business_only_newest_first(world):
    resp = queue(world)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [(r["module"], r["item_name"]) for r in body["items"]] == [
        ("event", "Event a"), ("service", "Service a"), ("product", "Product a"), ("training", "Training a"),
    ]
    assert body["counts"] == {"pending": 2, "approved": 1, "rejected": 1}
    assert body["pagination"]["total"] == 4


def test_a_super_admin_sees_every_business(world):
    body = queue(world, ROOT).json()

    assert body["pagination"]["total"] == 8
    assert {r["item_name"] for r in body["items"]} >= {"Training a", "Training b", "Event b"}


def test_another_business_sees_only_its_own(world):
    names = {r["item_name"] for r in queue(world, staff("admin", B)).json()["items"]}

    assert names == {"Training b", "Product b", "Service b", "Event b"}


def test_a_provider_can_read_but_a_customer_cannot_and_anonymous_is_401(world):
    assert queue(world, staff("provider")).status_code == 200
    assert queue(world, CUSTOMER).status_code == 403
    world.client.app.dependency_overrides[get_optional_catalog_user] = lambda: None
    assert world.client.get("/api/v1/reviews/manage").status_code in (401, 403)


# --- filters -------------------------------------------------------------------------------------

@pytest.mark.parametrize("module,expected", [
    ("training", ["Training a"]), ("course", ["Training a"]), ("product", ["Product a"]),
    ("service", ["Service a"]), ("event", ["Event a"]),
])
def test_filter_by_module(world, module, expected):
    assert [r["item_name"] for r in queue(world, module=module).json()["items"]] == expected


def test_filter_by_status_keeps_the_counts_for_all_tabs(world):
    body = queue(world, status="pending").json()

    assert sorted(r["item_name"] for r in body["items"]) == ["Event a", "Training a"]
    assert body["counts"] == {"pending": 2, "approved": 1, "rejected": 1}
    assert body["pagination"]["total"] == 2


def test_filter_by_item_rating_and_text(world):
    ids = world.ids.a
    assert [r["module"] for r in queue(world, item_id=str(ids.product)).json()["items"]] == ["product"]
    assert [r["module"] for r in queue(world, rating=2).json()["items"]] == ["event"]
    assert [r["module"] for r in queue(world, q="SOLID").json()["items"]] == ["product"]


def test_paging_merges_the_modules_in_order(world):
    first = queue(world, page=1, page_size=3).json()
    second = queue(world, page=2, page_size=3).json()

    assert [r["module"] for r in first["items"]] == ["event", "service", "product"]
    assert [r["module"] for r in second["items"]] == ["training"]
    assert first["pagination"] == {"total": 4, "page": 1, "page_size": 3, "total_pages": 2}


@pytest.mark.parametrize("params", [{"module": "program"}, {"status": "published"}, {"rating": 9}])
def test_bad_filters_are_422(world, params):
    assert queue(world, **params).status_code == 422


# --- the rows ------------------------------------------------------------------------------------

def test_rows_carry_names_never_emails_as_names_and_emails_only_where_known(world):
    rows = {r["module"]: r for r in queue(world).json()["items"]}

    assert rows["training"]["reviewer_name"] == "Learner a" and rows["training"]["reviewer_email"] == "l-a@example.com"
    assert rows["event"]["reviewer_name"] == "e-a" and rows["event"]["reviewer_email"] == "e-a@example.com"
    assert rows["product"]["reviewer_name"] == "Ravi" and rows["product"]["reviewer_email"] is None
    assert rows["service"]["reviewer_name"] is None  # its stored "name" was an email
    assert rows["product"]["is_verified"] is True and rows["service"]["is_verified"] is False
    assert rows["training"]["rating"] == 5 and isinstance(rows["event"]["rating"], int)


# --- moderate by module + id ---------------------------------------------------------------------

def review_id(world, module, tag="a"):
    items = queue(world, ROOT).json()["items"]
    name = {"training": "Training", "product": "Product", "service": "Service", "event": "Event"}[module] + f" {tag}"
    return next(r["review_id"] for r in items if r["item_name"] == name)


@pytest.mark.parametrize("module", ["training", "course", "product", "service", "event"])
def test_an_admin_can_approve_a_review_of_every_module(world, module, dispatched):
    rid = review_id(world, "training" if module == "course" else module)
    world.as_user(staff())

    resp = world.client.patch(f"/api/v1/reviews/{module}/{rid}/moderate", json={"action": "approved"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["review_id"] == rid and resp.json()["moderation_status"] == "approved"
    decisions = [a for n, a in dispatched if n == "_deliver_decision"]
    # product's review was already approved in the seed data, so nothing new to announce for it
    assert len(decisions) == (0 if module == "product" else 1)


@pytest.mark.parametrize("module", ["training", "product", "service", "event"])
def test_another_business_cannot_moderate_it(world, module):
    rid = review_id(world, module)
    world.as_user(staff("admin", B))

    resp = world.client.patch(f"/api/v1/reviews/{module}/{rid}/moderate", json={"action": "approved"})

    assert resp.status_code == 403


@pytest.mark.parametrize("module", ["training", "product", "service", "event"])
def test_providers_and_customers_cannot_moderate_and_a_super_admin_can(world, module):
    rid = review_id(world, module)
    for user in (staff("provider"), CUSTOMER):
        world.as_user(user)
        assert world.client.patch(f"/api/v1/reviews/{module}/{rid}/moderate", json={"action": "rejected"}).status_code == 403, user
    world.as_user(ROOT)
    assert world.client.patch(f"/api/v1/reviews/{module}/{rid}/moderate", json={"action": "rejected"}).status_code == 200


def test_unknown_review_module_and_action(world):
    rid = review_id(world, "training")
    patch = lambda module, rid_, action: world.client.patch(f"/api/v1/reviews/{module}/{rid_}/moderate", json={"action": action})

    assert patch("training", uuid4(), "approved").status_code == 404
    assert patch("program", rid, "approved").status_code == 422
    assert patch("training", rid, "published").status_code == 400
    # a product review id used with the training module is not found
    assert patch("training", review_id(world, "product"), "approved").status_code == 404


def test_the_event_feedback_rows_are_not_reviews(world):
    row = EventFeedback(event_id=world.ids.a.event, participant_email="x@example.com", is_review=False)
    world.db.add(row)
    world.db.commit()

    assert world.client.patch(f"/api/v1/reviews/event/{row.id}/moderate", json={"action": "approved"}).status_code == 404


# --- the business filter and the business columns (the Super Admin's global page) -----------------

def test_every_row_says_which_business_it_belongs_to(world):
    rows = queue(world, ROOT).json()["items"]

    assert {(r["business_name"], str(r["tenant_id"])) for r in rows} == {("Business A", str(A)), ("Business B", str(B))}
    assert all(r["business_name"] == "Business A" for r in queue(world, staff()).json()["items"])


@pytest.mark.parametrize("module", [None, "training", "product", "service", "event"])
def test_a_super_admin_can_pick_one_business(world, module):
    params = {"tenant_id": str(B)} if module is None else {"tenant_id": str(B), "module": module}

    body = queue(world, ROOT, **params).json()

    assert body["items"] and {r["business_name"] for r in body["items"]} == {"Business B"}
    assert body["pagination"]["total"] == len(body["items"]) == (4 if module is None else 1)


def test_the_business_filter_scopes_the_counts_and_pages_too(world):
    everything = queue(world, ROOT).json()["counts"]
    only_b = queue(world, ROOT, tenant_id=str(B)).json()

    assert sum(everything.values()) == 8 and sum(only_b["counts"].values()) == 4
    assert only_b["counts"] == {"pending": 2, "approved": 1, "rejected": 1}
    assert queue(world, ROOT, tenant_id=str(B), status="rejected").json()["pagination"]["total"] == 1


def test_a_business_with_no_reviews_gives_an_empty_queue(world):
    body = queue(world, ROOT, tenant_id=str(uuid4())).json()

    assert body["items"] == [] and body["counts"] == {"pending": 0, "approved": 0, "rejected": 0}


def test_an_admin_may_name_only_their_own_business(world):
    assert queue(world, staff(), tenant_id=str(A)).json()["pagination"]["total"] == 4
    assert queue(world, staff(), tenant_id=str(B)).status_code == 403
    assert queue(world, staff("provider"), tenant_id=str(B)).status_code == 403
