"""Checkout across catalog + commerce (design D1) vs. the monolith.

Products live in catalog, carts and orders in commerce. The flow compares every response (product reads show
the stock before checkout, after checkout and after cancellation), then checks what D1 adds underneath:
the reservation is confirmed and commerce's outbox has delivered everything.
"""
import json

from flows import run_flow

TENANT = "7a7a7a7a-0000-4000-8000-000000000001"
ADDRESS = {"full_name": "Ravi K", "phone": "9876543210", "line1": "1 MG Road", "city": "Pune", "state": "MH", "zip": "411001", "country": "India"}

STEPS = [
    {"as": "admin", "method": "POST", "path": "/api/v1/enterprises/", "expect": 201,
     "json": {"business_short_name": "Asha Pharmacy", "business_email": "pharmacy@example.com", "tenant_id": TENANT, "status": "active"}, "save": {"ent": "id"}},
    {"as": "admin", "method": "POST", "path": "/api/v1/products/", "expect": 201,
     "json": {"enterprise_id": "{ent}", "product_name": "Vitamin C", "category": "supplements", "price": 120.0, "stock_quantity": 5}, "save": {"p1": "id"}},
    {"as": "admin", "method": "POST", "path": "/api/v1/products/", "expect": 201,
     "json": {"enterprise_id": "{ent}", "product_name": "Zinc", "category": "supplements", "price": 80.0}, "save": {"p2": "id"}},
    {"as": "customer", "method": "POST", "path": "/api/v1/cart/", "json": {"product_id": "{p1}", "quantity": 9}},          # more than in stock
    {"as": "customer", "method": "POST", "path": "/api/v1/cart/", "json": {"product_id": "{p1}", "quantity": 3}},
    {"as": "customer", "method": "POST", "path": "/api/v1/cart/", "json": {"product_id": "{p2}", "quantity": 2}},
    {"as": "customer", "method": "GET", "path": "/api/v1/cart/"},
    {"as": "customer", "method": "POST", "path": "/api/v1/cart/checkout", "json": {"shipping_address": ADDRESS}, "save": {"order": "id"}},
    {"as": None, "method": "GET", "path": "/api/v1/products/{p1}"},                                                       # stock 5 -> 2
    {"as": "customer", "method": "POST", "path": "/api/v1/cart/checkout", "json": {"shipping_address": ADDRESS}},           # cart is empty now
    {"as": "customer", "method": "GET", "path": "/api/v1/cart/orders"},
    {"as": "customer", "method": "GET", "path": "/api/v1/cart/orders/{order}"},
    {"as": "customer", "method": "POST", "path": "/api/v1/cart/", "json": {"product_id": "{p1}", "quantity": 3}},          # only 2 left
    {"as": "customer", "method": "POST", "path": "/api/v1/cart/orders/{order}/cancel", "json": {"reason": "changed my mind"}},
    {"as": "customer", "method": "GET", "path": "/api/v1/cart/orders/{order}"},
    {"as": None, "method": "GET", "path": "/api/v1/products/{p1}"},                                                       # restocked
    {"as": "customer", "method": "POST", "path": "/api/v1/cart/", "json": {"product_id": "{p1}", "quantity": 3}},          # possible again
]


def test_checkout_and_cancellation_behave_like_the_monolith(stack):
    monolith, gateway, direct = stack
    diffs, log, sides = run_flow(monolith, gateway, STEPS)
    assert not diffs, json.dumps(diffs, indent=1)
    statuses = [row[3] for row in log]
    assert statuses.count(200) + statuses.count(201) >= 12, log
    pytest_saved = sides[1]["saved"]
    # D1 underneath: catalog's reservation for the order was confirmed, nothing waits in commerce's outbox
    from catalog_service.db.database import SessionLocal as CatalogSession
    from catalog_service.models.stock_reservation_model import StockReservation
    from commerce_service.outbox_setup import OUTBOX

    with CatalogSession() as db:
        reservations = db.query(StockReservation).all()
    assert [r.status for r in reservations] == ["confirmed"], [(r.status, r.order_ref) for r in reservations]
    assert reservations[0].order_ref == pytest_saved["order"]
    assert OUTBOX.pending_count() == 0


def test_a_failed_order_gives_the_stock_back(stack, monkeypatch):
    """If commerce fails after catalog reserved the stock, the reservation is released at once."""
    import pytest

    from catalog_service.db.database import SessionLocal as CatalogSession
    from catalog_service.models.product_model import Product
    from commerce_service.services import cart_service
    import compare_base as cb

    _m, gateway, _d = stack
    headers = {"Authorization": "Bearer " + cb.make_token(cb.USERS["admin"])}
    with CatalogSession() as db:
        product = db.query(Product).filter(Product.product_name == "Vitamin C").first()
        before = product.stock_quantity
    assert gateway.post("/api/v1/cart/", json={"product_id": str(product.id), "quantity": 1}, headers=headers).status_code in (200, 201)

    def broken(*a, **k):
        raise RuntimeError("commerce database went away")

    monkeypatch.setattr(cart_service, "create_order", broken)
    r = gateway.post("/api/v1/cart/checkout", json={"shipping_address": ADDRESS}, headers=headers)
    assert r.status_code == 500
    with CatalogSession() as db:
        assert db.get(Product, product.id).stock_quantity == before
    assert pytest is not None
