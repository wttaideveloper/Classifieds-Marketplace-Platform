"""Design D2 end to end: commerce's payment success / refund reaches the events service's event order through
commerce's outbox, exactly once; plus the replication safety net for bulk updates."""
import uuid
from datetime import datetime

from types import SimpleNamespace


def _event_order():
    """An event order as the events service stores it (created directly: the paid-registration flow is events' own)."""
    from events_service.db.database import SessionLocal
    from events_service.models.event_aux_models import EventOrder
    from events_service.models.event_model import Event

    with SessionLocal() as db:
        event = Event(title="Paid Workshop", category="health", start_date=datetime(2027, 5, 1, 9), end_date=datetime(2027, 5, 1, 12),
                      status="published")
        db.add(event)
        db.flush()
        cols = {c.key for c in EventOrder.__table__.columns}
        values = {"event_id": event.id, "participant_email": "ravi@example.com", "status": "pending", "payment_status": "pending",
                  "amount": "500.00", "currency": "INR", "ticket_subtotal": "500.00"}
        order = EventOrder(**{k: v for k, v in values.items() if k in cols})
        for col in EventOrder.__table__.columns:   # fill any other NOT NULL column without a default
            if not col.nullable and col.default is None and col.server_default is None and not col.primary_key and getattr(order, col.key) is None:
                setattr(order, col.key, "x" if "char" in str(col.type).lower() or "str" in str(col.type).lower() else 0)
        db.add(order)
        db.commit()
        return order.id


def _status(order_id):
    from events_service.db.database import SessionLocal
    from events_service.models.event_aux_models import EventOrder

    with SessionLocal() as db:
        o = db.get(EventOrder, order_id)
        return o.status, o.payment_status, o.payment_provider


def test_payable_view_comes_from_events(stack):
    _m, _g, direct = stack
    order_id = _event_order()
    r = direct["events"].get(f"/internal/v1/event-orders/{order_id}", headers={"X-Internal-Api-Key": "v2-internal-test-key"})
    assert r.status_code == 200
    body = r.json()
    assert body["payment_status"] == "pending" and body["event"]["title"] == "Paid Workshop" and body["ticket_subtotal"] == "500.00"


def test_payment_success_and_refund_reach_events_once(stack):
    from commerce_service.db.database import SessionLocal
    from commerce_service.outbox_setup import OUTBOX
    from commerce_service.services import payment_payables

    order_id = _event_order()
    payment = SimpleNamespace(id=uuid.uuid4(), purchase_type="event_order", purchase_id=str(order_id))
    with SessionLocal() as db:
        payment_payables.on_paid(db, payment)     # inside the payment's transaction
        assert _status(order_id)[1] == "pending"  # nothing leaves before the commit
        db.commit()                               # -> outbox fast path -> events
    assert _status(order_id)[1:] == ("confirmed", "marketplace")
    assert OUTBOX.pending_count() == 0

    # a duplicate delivery (e.g. the relay retrying after a lost answer) changes nothing
    from mskit import internal

    internal.call("events", "POST", f"/event-orders/{order_id}/payment-confirmed", json={"payment_id": str(payment.id)},
                  headers={"Idempotency-Key": f"payment:{payment.id}:confirmed"})
    assert _status(order_id)[1] == "confirmed"

    with SessionLocal() as db:
        payment_payables.on_refunded(db, payment, True)
        db.commit()
    assert _status(order_id)[:2] == ("refunded", "refunded")


def test_rolled_back_payment_tells_events_nothing(stack):
    from commerce_service.db.database import SessionLocal
    from commerce_service.services import payment_payables

    order_id = _event_order()
    with SessionLocal() as db:
        payment_payables.on_paid(db, SimpleNamespace(id=uuid.uuid4(), purchase_type="event_order", purchase_id=str(order_id)))
        db.rollback()
    assert _status(order_id)[1] == "pending"


def test_bulk_update_in_catalog_still_reaches_the_replicas(stack):
    from catalog_service.db.database import SessionLocal as CatalogSession
    from catalog_service.models.product_model import Product
    from commerce_service.db.database import SessionLocal as CommerceSession
    from commerce_service.models.product_model import Product as ReplicaProduct

    with CatalogSession() as db:
        product_id = db.query(Product).first().id
        db.query(Product).filter(Product.id == product_id).update({Product.product_name: "Renamed in bulk"}, synchronize_session=False)
        db.commit()
    with CommerceSession() as db:
        assert db.get(ReplicaProduct, product_id).product_name == "Renamed in bulk"
