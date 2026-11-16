"""Orders for the Enterprise Admin: the list and the detail of the tenant's orders (a customer still sees only their own), the cancellation-request list, the
fulfilment fields the customer sees, and that the admins are told about a customer's cancellation (the identity lookup gets the caller's bearer token).
"""
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.api.v1.endpoints.cart import router
from app.core.dependencies import get_current_user
from app.models.cart_model import OrderStatusEvent
from tests.pay_support import TENANT_A, TENANT_B, cart_order, notices_of, world  # noqa: F401
from tests.test_pay_refunds import paid

ORDERS = "/cart/orders"


@pytest.fixture
def ow(world):
    OrderStatusEvent.__table__.create(world.db.get_bind(), checkfirst=True)
    world.app.include_router(router, prefix="/cart")

    def act(user):
        world.app.dependency_overrides[get_current_user] = lambda: user
        return world.as_user(user)

    world.act = act
    return world


def three_orders(w):
    p = w.people
    a = cart_order(w, p.priya, total="500", items=[("Yoga mat", "500", 1)])
    b = cart_order(w, p.ravi, total="300", status="confirmed", items=[("Resistance band", "300", 1)])
    foreign = cart_order(w, p.ravi, total="900", tenant=TENANT_B, items=[("Foam roller", "900", 1)])
    return a, b, foreign


# ---------------------------------------------------------------- the list

def test_the_enterprise_admin_sees_the_tenants_orders_and_nobody_elses(ow):
    a, b, foreign = three_orders(ow)

    admin = ow.act(ow.people.asha).get(ORDERS).json()

    assert admin["total"] == 2 and {i["id"] for i in admin["items"]} == {str(a.id), str(b.id)}
    first = next(i for i in admin["items"] if i["id"] == str(a.id))
    assert first["customer"] == {"user_id": ow.people.priya["id"], "name": "Priya Customer", "phone": None} and first["items_preview"] == ["Yoga mat"]
    assert first["status"] == "pending" and first["payment_status"] and first["total"] == 500 and first["item_count"] == 1 and first["carrier"] is None
    other = ow.act(ow.people.bela).get(ORDERS).json()
    assert other["total"] == 1 and other["items"][0]["id"] == str(foreign.id)
    assert ow.act(ow.people.root).get(ORDERS).json()["total"] == 3
    assert ow.act(ow.people.root).get(ORDERS, params={"tenant_id": str(TENANT_B)}).json()["total"] == 1


def test_a_customer_still_sees_only_their_own_orders_in_the_customer_shape(ow):
    a, b, foreign = three_orders(ow)

    mine = ow.act(ow.people.priya).get(ORDERS).json()

    assert [i["id"] for i in mine["items"]] == [str(a.id)] and "customer" not in mine["items"][0] and mine["items"][0]["item_count"] == 1
    assert ow.act(ow.people.ravi).get(ORDERS).json()["pagination"]["total"] == 2           # his two (one is another tenant's shop)
    assert ow.act(ow.people.asha).get(ORDERS, params={"mine": "true"}).json()["pagination"]["total"] == 0   # the admin's own purchases: none


def test_the_admin_list_filters_and_pages(ow):
    a, b, _ = three_orders(ow)
    admin = ow.act(ow.people.asha)

    assert [i["id"] for i in admin.get(ORDERS, params={"status": "confirmed"}).json()["items"]] == [str(b.id)]
    assert [i["id"] for i in admin.get(ORDERS, params={"q": "priya"}).json()["items"]] == [str(a.id)]
    assert [i["id"] for i in admin.get(ORDERS, params={"q": "resistance"}).json()["items"]] == [str(b.id)]
    assert [i["id"] for i in admin.get(ORDERS, params={"q": str(a.id)[:8]}).json()["items"]] == [str(a.id)]
    today = datetime.utcnow().date().isoformat()
    assert admin.get(ORDERS, params={"date_from": today, "date_to": today}).json()["total"] == 2
    assert admin.get(ORDERS, params={"date_from": (datetime.utcnow().date() + timedelta(days=1)).isoformat()}).json()["total"] == 0
    page = admin.get(ORDERS, params={"page_size": 1, "page": 2}).json()
    assert page["total"] == 2 and len(page["items"]) == 1 and page["page"] == 2


# ---------------------------------------------------------------- the detail

def test_the_admin_opens_a_customers_order_and_others_get_404(ow):
    a, b, foreign = three_orders(ow)
    path = f"{ORDERS}/{a.id}"

    seen = ow.act(ow.people.asha).get(path)

    body = seen.json()
    assert seen.status_code == 200 and body["id"] == str(a.id) and body["customer"]["name"] == "Priya Customer" and body["allowed_statuses"] == ["confirmed", "cancelled"]
    assert body["items"][0]["product_name"] == "Yoga mat" and body["history"] == [] and body["carrier"] is None
    assert ow.act(ow.people.root).get(path).status_code == 200
    for stranger in (ow.people.bela, ow.people.ravi):
        assert ow.act(stranger).get(path).status_code == 404, stranger["name"]
    assert ow.act(ow.people.asha).get(f"{ORDERS}/{foreign.id}").status_code == 404           # another tenant's order
    assert ow.act(ow.people.asha).get(f"{ORDERS}/{uuid4()}").status_code == 404
    own = ow.act(ow.people.priya).get(path).json()
    assert own["can_cancel"] is True and own["customer"] is None and own["allowed_statuses"] is None


def test_the_customer_sees_the_tracking_the_admin_entered(ow):
    ow.act(ow.people.asha)
    order = cart_order(ow, ow.people.priya, total="500", status="confirmed")

    refused = ow.act(ow.people.asha).patch(f"{ORDERS}/{order.id}/status", json={"status": "shipped"})
    shipped = ow.act(ow.people.asha).patch(f"{ORDERS}/{order.id}/status", json={"status": "shipped", "carrier": "Blue Dart", "tracking_number": "BD123", "tracking_url": "https://t.example/BD123"})

    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "TRACKING_REQUIRED" and shipped.status_code == 200
    mine = ow.act(ow.people.priya).get(f"{ORDERS}/{order.id}").json()
    assert mine["status"] == "shipped" and mine["carrier"] == "Blue Dart" and mine["tracking_number"] == "BD123" and mine["tracking_url"] == "https://t.example/BD123"
    assert mine["shipped_at"] and [h["action"] for h in mine["history"]] == ["shipped"] and mine["history"][0]["actor_role"] == "admin"
    assert ow.act(ow.people.asha).get(f"{ORDERS}/{order.id}").json()["allowed_statuses"] == ["delivered"]


# ---------------------------------------------------------------- cancellation requests

def test_cancellation_requests_are_listed_for_the_admin_only(ow):
    a, b, foreign = three_orders(ow)
    for row in (b, foreign):
        row.status, row.cancellation_requested_at, row.cancel_reason = "cancellation_requested", datetime.utcnow(), "changed my mind"
    ow.db.commit()

    asha = ow.act(ow.people.asha).get(f"{ORDERS}/cancellation-requests")

    assert asha.status_code == 200 and [i["id"] for i in asha.json()["items"]] == [str(b.id)] and asha.json()["items"][0]["cancellation_requested_at"]
    assert [i["id"] for i in ow.act(ow.people.bela).get(f"{ORDERS}/cancellation-requests").json()["items"]] == [str(foreign.id)]
    assert ow.act(ow.people.priya).get(f"{ORDERS}/cancellation-requests").status_code == 403
    decided = ow.act(ow.people.asha).post(f"{ORDERS}/{b.id}/cancel/decision", json={"action": "reject", "reason": "Already packed"})
    assert decided.status_code == 200 and decided.json()["status"] == "confirmed"
    assert ow.act(ow.people.asha).get(f"{ORDERS}/cancellation-requests").json()["items"] == []


# ---------------------------------------------------------------- telling the admins

def paid_confirmed_order(w):
    order, payment = paid(w)
    order.status, order.delivered_at = "confirmed", None
    order.shipping_phone, order.shipping_line1, order.shipping_city, order.shipping_zip = "12345678", "12 Test Way", "Pune", "411001"
    w.db.commit()
    return order


def test_a_cancellation_request_tells_the_admins_and_the_lookup_gets_the_callers_token(ow, monkeypatch):
    lookups = []

    def admins(_db, item, access_token=None):
        lookups.append(access_token)
        return ([UUID(ow.people.asha["id"])] if access_token else []), item.tenant_id        # the identity service answers only with a bearer token

    monkeypatch.setattr("app.services.event_notification_service.resolve_enterprise_admin_user_ids", admins)
    order = paid_confirmed_order(ow)
    ow.notices.clear()
    lookups.clear()                                                                          # (paying the order looked up the admins too)

    resp = ow.act(ow.people.priya).post(f"{ORDERS}/{order.id}/cancel", json={"reason": "Ordered by mistake"}, headers={"Authorization": "Bearer customer-token"})

    assert resp.status_code == 200 and resp.json()["status"] == "cancellation_requested"
    assert lookups == ["customer-token"]
    [note] = notices_of(ow, "order_cancellation_requested")
    assert note["user_ids"] == [UUID(ow.people.asha["id"])] and note["metadata"]["order_id"] == str(order.id) and note["metadata"]["entity_type"] == "order"
    assert note["metadata"]["category"] == "order_cancellation_requested" and note["channels"] == ["in_app", "push"]


def test_a_customer_cancelling_an_unpaid_order_tells_the_admins_too(ow, monkeypatch):
    lookups = []
    monkeypatch.setattr("app.services.event_notification_service.resolve_enterprise_admin_user_ids",
                        lambda _db, item, access_token=None: lookups.append(access_token) or ([UUID(ow.people.asha["id"])], item.tenant_id))
    order = cart_order(ow, ow.people.priya, total="500")
    order.shipping_phone, order.shipping_line1, order.shipping_city, order.shipping_zip = "12345678", "12 Test Way", "Pune", "411001"
    ow.db.commit()
    ow.notices.clear()

    resp = ow.act(ow.people.priya).post(f"{ORDERS}/{order.id}/cancel", json={}, headers={"Authorization": "Bearer customer-token"})

    assert resp.status_code == 200 and resp.json()["status"] == "cancelled" and lookups == ["customer-token"]
    assert sorted(str(u) for n in notices_of(ow, "order_cancelled") for u in n["user_ids"]) == sorted([ow.people.priya["id"], ow.people.asha["id"]])


def test_when_the_admins_cannot_be_found_the_failure_is_logged_not_hidden(ow, monkeypatch, caplog):
    def broken(_db, item, access_token=None):
        raise RuntimeError("identity service said 401")

    monkeypatch.setattr("app.services.event_notification_service.resolve_enterprise_admin_user_ids", broken)
    order = paid_confirmed_order(ow)

    with caplog.at_level("WARNING"):
        resp = ow.act(ow.people.priya).post(f"{ORDERS}/{order.id}/cancel", json={"reason": "Ordered by mistake"})

    assert resp.status_code == 200 and resp.json()["status"] == "cancellation_requested"          # the customer's call still works
    assert any("could not list the admins of order" in r.getMessage() for r in caplog.records)
