"""Shared setup for the payment, currency, tax, invoice, refund and settlement tests: an in-memory database with the real
routes, the three kinds of caller, the test gateway, and everything that leaves the process captured."""
import importlib
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.catalog_access import CatalogAccess, get_catalog_access, get_optional_catalog_user
from app.core.config import settings
from app.db.database import Base, get_db
from app.models import payment_model
from app.models.cart_model import Cart, CartItem, Order, OrderItem
from app.models.enterprise_model import Enterprise
from app.models.event_aux_models import EventOrder
from app.models.event_model import Event
from app.models.notification_model import NotificationEventLog
from app.models.payment_model import Currency, ExchangeRate

TENANT_A, TENANT_B = uuid4(), uuid4()
ROUTERS = [
    ("currency", "/currencies"), ("tax", "/tax"), ("payment", "/payments"), ("payment_method", "/payment-methods"),
    ("invoice", "/invoices"), ("refund", "/refunds"), ("settlement", "/settlements"), ("payment_report", "/payment-reports"),
    ("finance", "/finance"),
]
PAY_TABLES = [m.__table__ for m in vars(payment_model).values() if isinstance(m, type) and issubclass(m, Base) and m is not Base]


def person(name, role, tenant=None):
    return {"id": str(uuid4()), "name": name, "email": f"{name.lower().replace(' ', '.')}@example.test", "role": role,
            "status": "active", "tenant_id": str(tenant) if tenant else None}


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from app.models.product_model import Product

    tables = PAY_TABLES + [m.__table__ for m in (Enterprise, NotificationEventLog, Order, OrderItem, Cart, CartItem, Product, Event, EventOrder)]
    Base.metadata.create_all(engine, tables=tables)
    db = sessionmaker(bind=engine)()

    root = person("Platform Root", "super_admin")
    root2 = person("Second Root", "super_admin")
    asha = person("Asha Merchant", "admin", TENANT_A)
    bela = person("Bela Merchant", "admin", TENANT_B)
    priya = person("Priya Customer", "customer")
    ravi = person("Ravi Customer", "customer")
    people = SimpleNamespace(root=root, root2=root2, asha=asha, bela=bela, priya=priya, ravi=ravi)

    monkeypatch.setattr(settings, "PAYMENT_BASE_CURRENCY", "INR")
    monkeypatch.setattr(settings, "PAYMENT_GATEWAY", "test")
    for order, (code, name, decimals, active) in enumerate([
        ("INR", "Indian Rupee", 2, True), ("USD", "US Dollar", 2, True), ("EUR", "Euro", 2, True), ("JPY", "Japanese Yen", 0, True),
        ("KWD", "Kuwaiti Dinar", 3, True), ("GBP", "Pound Sterling", 2, False),
    ]):
        db.add(Currency(code=code, name=name, decimals=decimals, is_active=active, sort_order=order))
    for quote, rate in (("USD", "0.012"), ("EUR", "0.011"), ("JPY", "1.8"), ("KWD", "0.0037"), ("GBP", "0.0095")):
        db.add(ExchangeRate(base_currency="INR", quote_currency=quote, rate=Decimal(rate), source="seed"))
    db.add(Enterprise(tenant_id=TENANT_A, business_short_name="Calm Clinic", business_legal_name="Calm Clinic Pvt Ltd", business_email="calm@example.test"))
    db.add(Enterprise(tenant_id=TENANT_B, business_short_name="Bela Wellness", business_legal_name="Bela Wellness Ltd", business_email="bela@example.test"))
    db.commit()

    notices: list[dict] = []
    monkeypatch.setattr(
        "app.services.notification_service.create_automatic_notification",
        lambda _db, **kw: notices.append(kw) or SimpleNamespace(id=uuid4()),
    )
    monkeypatch.setattr("app.services.event_notification_service.resolve_platform_admin_user_ids",
                        lambda token=None: [UUID(root["id"]), UUID(root2["id"])])
    monkeypatch.setattr(
        "app.services.event_notification_service.resolve_enterprise_admin_user_ids",
        lambda _db, item, access_token=None: (
            [UUID(a["id"]) for a in (asha, bela) if a["tenant_id"] == str(getattr(item, "tenant_id", ""))], getattr(item, "tenant_id", None)),
    )

    @contextmanager
    def session_factory():
        yield db

    monkeypatch.setattr("app.db.database.SessionLocal", session_factory)
    from app.services import payment_notifications

    monkeypatch.setattr(payment_notifications, "_dispatch", lambda fn, *a, **k: fn(*a, **k))

    app = FastAPI()
    for module, prefix in ROUTERS:
        try:
            mod = importlib.import_module(f"app.api.v1.endpoints.{module}")
        except ModuleNotFoundError as exc:
            if exc.name != f"app.api.v1.endpoints.{module}":
                raise
            continue
        app.include_router(mod.router, prefix=prefix)
    state = {"user": None}

    def access():
        user = state["user"]
        if user is None:
            return CatalogAccess("public")
        role = user["role"] if user["role"] in ("super_admin", "admin", "customer") else "customer"
        return CatalogAccess(role, UUID(user["tenant_id"]) if role == "admin" else None)

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


def notices_of(w, category):
    return [n for n in w.notices if n["category"] == category]


# ---------------------------------------------------------------- helpers the tests share

def post_rule(w, **extra):
    body = {"tax_type": "gst", "name": "GST", "country": "IN", "rate": "18", **extra}
    resp = w.as_user(w.people.root).post("/tax/rules", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def india_gst(w):
    """CGST + SGST 9% each inside a state, IGST 18% between states; the merchant (tenant A) supplies from Karnataka."""
    for name, rate, condition in (("CGST", "9", "intra_state"), ("SGST", "9", "intra_state"), ("IGST", "18", "inter_state")):
        post_rule(w, name=name, rate=rate, condition=condition)
    resp = w.as_user(w.people.asha).put("/tax/merchant-profile", json={"country": "IN", "state": "Karnataka"})
    assert resp.status_code == 200, resp.text


def cart_order(w, user, *, total="1000", currency="INR", tenant=TENANT_A, status="pending", state="Karnataka", country="India", items=None):
    from decimal import Decimal as D

    order = Order(user_id=UUID(user["id"]), tenant_id=tenant, status=status, total=float(total), currency=currency, shipping_country=country, shipping_state=state,
                  shipping_full_name=user["name"])
    w.db.add(order)
    w.db.flush()
    for name, price, qty in (items or [("Yoga mat", total, 1)]):
        w.db.add(OrderItem(order_id=order.id, product_id=uuid4(), product_name=name, quantity=qty, unit_price=float(price), line_total=float(D(str(price)) * qty), currency=currency))
    w.db.commit()
    return order


def event_order(w, user, *, amount="2000", currency="INR", tenant=TENANT_A, payment_status="pending"):
    from datetime import datetime, timedelta

    event = Event(tenant_id=tenant, title="Wellness Summit", description="d", category="Wellness", organiser_name="Org", organiser_contact="o@example.test",
                  start_date=datetime.utcnow() + timedelta(days=10), end_date=datetime.utcnow() + timedelta(days=11), time_zone="UTC", delivery_mode="in_person",
                  pricing_type="paid", ticket_types=[], sessions=[], status="published")
    w.db.add(event)
    w.db.flush()
    order = EventOrder(event_id=event.id, participant_name=user["name"], participant_email=user["email"], quantity="1", amount=amount, currency=currency,
                       payment_status=payment_status, status="confirmed", ticket_subtotal=amount, meal_subtotal="0", accommodation_subtotal="0")
    w.db.add(order)
    w.db.commit()
    return order


def pay(w, user, purchase, *, token="tok_success", currency=None, country="IN", state="Karnataka", key=None, **extra):
    """Create a payment for a cart / event order and report it to the gateway as paid with the given test token."""
    kind = "event_order" if isinstance(purchase, EventOrder) else "cart_order"
    body = {"purchase_type": kind, "purchase_id": str(purchase.id), "billing": {"country": country, "state": state}, **extra}
    if currency:
        body["currency"] = currency
    headers = {"Idempotency-Key": key} if key else {}
    created = w.as_user(user).post("/payments", json=body, headers=headers)
    if created.status_code != 201 or token is None:
        return created
    payment_id = created.json()["payment"]["id"]
    return w.as_user(user).post(f"/payments/{payment_id}/verify", json={"test_token": token})
