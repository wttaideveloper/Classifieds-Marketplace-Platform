"""The moderation history (GET /reviews/audit) and deleting reviews: authors can delete their own training and event
reviews, staff can delete reviews of their own business, and every moderator action leaves a history row."""
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import event, product, review_admin, service, training
from app.core.catalog_access import get_optional_catalog_user
from app.core.dependencies import get_current_user
from app.db.database import Base, get_db
from app.models.event_aux_models import EventFeedback
from app.models.product_model import ProductReview
from app.models.review_audit_model import ReviewModerationLog
from app.models.service_model import ServiceReview
from app.models.training_model import TrainingReview
from tests.event_sql_support import make_session
from tests.test_reviews_queue import A, B, seed

AUTHORS = {
    "training": {"id": str(uuid4()), "role": "customer", "email": "l-a@example.com"},
    "event": {"id": str(uuid4()), "role": "customer", "email": "e-a@example.com"},
}
ROOT = {"id": str(uuid4()), "role": "super_admin", "email": "root@example.com"}


def staff(role="admin", tenant=A):
    return {"id": str(uuid4()), "email": f"{role}@example.com", "role": role, "tenant_id": str(tenant)}


@pytest.fixture
def world():
    db = make_session()
    Base.metadata.create_all(db.bind, tables=[ReviewModerationLog.__table__])
    ids = seed(db)
    app = FastAPI()
    app.include_router(review_admin.router, prefix="/api/v1/reviews")
    app.include_router(training.router, prefix="/api/v1/trainings")
    app.include_router(product.router, prefix="/api/v1/products")
    app.include_router(service.router, prefix="/api/v1/services")
    app.include_router(event.router, prefix="/api/v1/events")
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app, raise_server_exceptions=False)

    def as_user(user):
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_optional_catalog_user] = lambda: user
        return client

    as_user(staff())
    yield SimpleNamespace(client=client, as_user=as_user, db=db, ids=ids)
    db.close()


def review_id(world, module, tag="a"):
    items = world.as_user(ROOT).get("/api/v1/reviews/manage").json()["items"]
    world.as_user(staff())
    return next(r["review_id"] for r in items if r["module"] == module and r["item_name"].endswith(f" {tag}"))


def history(world, **filters):
    rows = world.db.query(ReviewModerationLog)
    for key, value in filters.items():
        rows = rows.filter(getattr(ReviewModerationLog, key) == value)
    return rows.order_by(ReviewModerationLog.created_at, ReviewModerationLog.id).all()


MODULE_PATHS = {"training": "trainings", "product": "products", "service": "services", "event": "events"}


def item_of(world, module, tag="a"):
    ids = getattr(world.ids, tag)
    return str(getattr(ids, module))


# --- the history is written ----------------------------------------------------------------------

@pytest.mark.parametrize("module", ["training", "product", "service", "event"])
def test_every_module_logs_a_moderator_action(world, module):
    rid = review_id(world, module)
    admin = staff()
    world.as_user(admin)
    # whatever status the seeded review has, ask for the other one
    current = world.db.get({"training": TrainingReview, "product": ProductReview, "service": ServiceReview, "event": EventFeedback}[module], UUID(rid))
    target = "rejected" if current.moderation_status != "rejected" else "approved"
    before = current.moderation_status

    resp = world.client.patch(
        f"/api/v1/{MODULE_PATHS[module]}/{item_of(world, module)}/reviews/{rid}/moderate", json={"action": target}
    )

    assert resp.status_code == 200, resp.text
    [row] = history(world, review_id=UUID(rid))
    assert (row.module, row.from_status, row.to_status) == (module, before, target)
    assert str(row.actor_user_id) == admin["id"] and row.actor_role == "admin"
    assert row.tenant_id == A and row.item_name.endswith(" a") and str(row.item_id) == item_of(world, module)


def test_only_real_changes_are_logged_and_each_change_adds_a_row(world):
    rid = review_id(world, "product")   # seeded as approved
    url = f"/api/v1/products/{world.ids.a.product}/reviews/{rid}/moderate"
    for action in ("approved", "pending", "pending", "rejected"):
        world.client.patch(url, json={"action": action})

    assert [(r.from_status, r.to_status) for r in history(world, review_id=UUID(rid))] == [
        ("approved", "pending"), ("pending", "rejected"),
    ]


def test_the_queue_route_logs_with_the_acting_user(world):
    rid = review_id(world, "training")
    admin = staff()
    world.as_user(admin)

    world.client.patch(f"/api/v1/reviews/training/{rid}/moderate", json={"action": "approved"})

    [row] = history(world, review_id=UUID(rid))
    assert row.to_status == "approved" and str(row.actor_user_id) == admin["id"]


def test_a_super_admins_action_is_logged_under_the_owning_business(world):
    rid = review_id(world, "event")
    world.as_user(ROOT)

    world.client.patch(f"/api/v1/events/{world.ids.a.event}/reviews/{rid}/moderate", json={"action": "approved"})

    [row] = history(world, review_id=UUID(rid))
    assert row.actor_role == "super_admin" and row.tenant_id == A


def test_a_problem_writing_the_history_never_blocks_the_moderator(world, monkeypatch):
    from app.services import review_audit

    class Broken:
        def __init__(self, **kwargs):
            raise RuntimeError("history table unavailable")

    monkeypatch.setattr(review_audit, "ReviewModerationLog", Broken)
    rid = review_id(world, "service")   # seeded as rejected

    resp = world.client.patch(f"/api/v1/services/{world.ids.a.service}/reviews/{rid}/moderate", json={"action": "approved"})

    assert resp.status_code == 200 and resp.json()["moderation_status"] == "approved"
    world.db.expire_all()
    assert world.db.get(ServiceReview, UUID(rid)).moderation_status == "approved"


# --- deleting ------------------------------------------------------------------------------------

@pytest.mark.parametrize("module", ["training", "event"])
def test_the_author_can_delete_their_own_training_or_event_review_without_a_history_row(world, module):
    rid = review_id(world, module)
    world.as_user(AUTHORS[module])

    resp = world.client.delete(f"/api/v1/{MODULE_PATHS[module]}/{item_of(world, module)}/reviews/{rid}")

    assert resp.status_code == 200 and resp.json() == {"message": "Review deleted"}
    assert history(world, review_id=UUID(rid)) == []
    world.as_user(ROOT)
    assert all(r["review_id"] != rid for r in world.client.get("/api/v1/reviews/manage").json()["items"])


@pytest.mark.parametrize("module", ["training", "event"])
def test_a_stranger_cannot_delete_someone_elses_review(world, module):
    rid = review_id(world, module)
    world.as_user({"id": str(uuid4()), "role": "customer", "email": "stranger@example.com"})

    resp = world.client.delete(f"/api/v1/{MODULE_PATHS[module]}/{item_of(world, module)}/reviews/{rid}")

    assert resp.status_code == 403 and resp.json()["detail"] == "You can only delete your own review"


@pytest.mark.parametrize("module", ["training", "product", "service", "event"])
def test_staff_of_the_owning_business_can_delete_and_it_is_logged(world, module):
    rid = review_id(world, module)
    provider = staff("provider")
    world.as_user(provider)

    resp = world.client.delete(f"/api/v1/{MODULE_PATHS[module]}/{item_of(world, module)}/reviews/{rid}")

    assert resp.status_code == 200, resp.text
    [row] = history(world, review_id=UUID(rid))
    assert row.to_status == "deleted" and str(row.actor_user_id) == provider["id"] and row.tenant_id == A


@pytest.mark.parametrize("module", ["training", "product", "service", "event"])
def test_staff_of_another_business_cannot_delete(world, module):
    rid = review_id(world, module)
    world.as_user(staff("admin", B))

    resp = world.client.delete(f"/api/v1/{MODULE_PATHS[module]}/{item_of(world, module)}/reviews/{rid}")

    assert resp.status_code == 403 and history(world, review_id=UUID(rid)) == []


@pytest.mark.parametrize("module", ["training", "product", "service", "event"])
def test_a_super_admin_can_delete_any_review(world, module):
    rid = review_id(world, module, tag="b")
    world.as_user(ROOT)

    resp = world.client.delete(f"/api/v1/{MODULE_PATHS[module]}/{item_of(world, module, 'b')}/reviews/{rid}")

    assert resp.status_code == 200
    [row] = history(world, review_id=UUID(rid))
    assert row.tenant_id == B and row.actor_role == "super_admin"


@pytest.mark.parametrize("module", ["training", "event"])
def test_deleting_an_unknown_review_or_one_from_another_item_is_404(world, module):
    rid = review_id(world, module)
    other = item_of(world, module, "b")
    world.as_user(ROOT)

    assert world.client.delete(f"/api/v1/{MODULE_PATHS[module]}/{item_of(world, module)}/reviews/{uuid4()}").status_code == 404
    assert world.client.delete(f"/api/v1/{MODULE_PATHS[module]}/{other}/reviews/{rid}").status_code == 404


def test_an_event_feedback_row_cannot_be_deleted_as_a_review(world):
    row = EventFeedback(event_id=world.ids.a.event, participant_email="x@example.com", is_review=False)
    world.db.add(row)
    world.db.commit()
    world.as_user(ROOT)

    assert world.client.delete(f"/api/v1/events/{world.ids.a.event}/reviews/{row.id}").status_code == 404


# --- reading the history -------------------------------------------------------------------------

def make_history(world):
    """Business A: approve a training review, reject an event review, delete a service review. Business B: approve one."""
    world.as_user(staff())
    world.client.patch(f"/api/v1/reviews/training/{review_id(world, 'training')}/moderate", json={"action": "approved"})
    world.client.patch(f"/api/v1/reviews/event/{review_id(world, 'event')}/moderate", json={"action": "rejected"})
    world.client.delete(f"/api/v1/services/{world.ids.a.service}/reviews/{review_id(world, 'service')}")
    rid_b = review_id(world, "training", "b")
    world.as_user(staff("admin", B))
    world.client.patch(f"/api/v1/reviews/training/{rid_b}/moderate", json={"action": "approved"})


def audit(world, user, **params):
    world.as_user(user)
    return world.client.get("/api/v1/reviews/audit", params=params)


def test_an_admin_sees_the_history_of_their_own_business_newest_first(world):
    make_history(world)

    body = audit(world, staff()).json()

    assert [(r["module"], r["to_status"]) for r in body["items"]] == [("service", "deleted"), ("event", "rejected"), ("training", "approved")]
    assert body["pagination"]["total"] == 3
    first = body["items"][0]
    assert first["from_status"] == "rejected" and first["actor_role"] == "admin" and first["item_name"] == "Service a"
    assert set(first) == {"id", "module", "review_id", "item_id", "item_name", "from_status", "to_status", "actor_user_id", "actor_role", "created_at"}


def test_a_super_admin_sees_every_business_and_another_business_sees_only_its_own(world):
    make_history(world)

    assert audit(world, ROOT).json()["pagination"]["total"] == 4
    other = audit(world, staff("admin", B)).json()
    assert [(r["module"], r["item_name"]) for r in other["items"]] == [("training", "Training b")]


def test_providers_and_customers_cannot_read_the_history(world):
    assert audit(world, staff("provider")).status_code == 403
    assert audit(world, {"id": str(uuid4()), "role": "customer", "email": "c@example.com"}).status_code == 403


def test_history_filters_and_paging(world):
    make_history(world)

    assert [r["module"] for r in audit(world, staff(), module="event").json()["items"]] == ["event"]
    assert [r["module"] for r in audit(world, staff(), action="deleted").json()["items"]] == ["service"]
    rid = review_id(world, "training")
    assert [r["module"] for r in audit(world, staff(), review_id=rid).json()["items"]] == ["training"]
    page = audit(world, staff(), page=2, page_size=2).json()
    assert [r["module"] for r in page["items"]] == ["training"]
    assert page["pagination"] == {"total": 3, "page": 2, "page_size": 2, "total_pages": 2}
    assert audit(world, staff(), module="program").status_code == 422
    assert audit(world, staff(), action="published").status_code == 422


# --- the business filter on the history -----------------------------------------------------------

def test_the_history_can_be_filtered_by_business(world):
    make_history(world)

    assert audit(world, ROOT, tenant_id=str(B)).json()["pagination"]["total"] == 1
    assert audit(world, ROOT, tenant_id=str(A)).json()["pagination"]["total"] == 3
    assert audit(world, staff(), tenant_id=str(A)).json()["pagination"]["total"] == 3
    assert audit(world, staff(), tenant_id=str(B)).status_code == 403


# --- delete from the queue: DELETE /reviews/{module}/{review_id} ----------------------------------

@pytest.mark.parametrize("module", ["training", "product", "service", "event"])
def test_staff_can_delete_a_review_given_only_its_module_and_id(world, module):
    rid = review_id(world, module)
    admin = staff()
    world.as_user(admin)

    resp = world.client.delete(f"/api/v1/reviews/{module}/{rid}")

    assert resp.status_code == 200 and resp.json() == {"message": "Review deleted"}
    [row] = history(world, review_id=UUID(rid))
    assert row.to_status == "deleted" and str(row.actor_user_id) == admin["id"]
    world.as_user(ROOT)
    assert all(r["review_id"] != rid for r in world.client.get("/api/v1/reviews/manage").json()["items"])


@pytest.mark.parametrize("module", ["training", "product", "service", "event"])
def test_the_same_delete_rules_apply_from_the_queue(world, module):
    rid = review_id(world, module)
    for user in (staff("admin", B), {"id": str(uuid4()), "role": "customer", "email": "stranger@example.com"}):
        world.as_user(user)
        assert world.client.delete(f"/api/v1/reviews/{module}/{rid}").status_code == 403, user
    assert history(world, review_id=UUID(rid)) == []
    world.as_user(ROOT)
    assert world.client.delete(f"/api/v1/reviews/{module}/{rid}").status_code == 200


def test_the_course_name_works_and_an_author_can_delete_their_own_without_a_history_row(world):
    rid = review_id(world, "training")
    world.as_user(AUTHORS["training"])

    assert world.client.delete(f"/api/v1/reviews/course/{rid}").status_code == 200
    assert history(world, review_id=UUID(rid)) == []


def test_deleting_from_the_queue_unknown_things(world):
    world.as_user(ROOT)
    rid = review_id(world, "training")

    assert world.client.delete(f"/api/v1/reviews/training/{uuid4()}").status_code == 404
    assert world.client.delete(f"/api/v1/reviews/program/{rid}").status_code == 422
    assert world.client.delete(f"/api/v1/reviews/product/{rid}").status_code == 404   # a training review id is not a product review
