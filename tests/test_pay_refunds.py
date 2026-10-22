"""Refunds: full and partial, asked by the customer or raised by the business, approval, destinations, the credit note, the tax
reversal, failure and retry, the target time and its escalation, and the gateway's refund webhooks."""
import json
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from app.models.cart_model import Order
from app.models.payment_model import FinanceAuditLog, Invoice, Payment, Refund, TaxTransaction
from app.services import refund_service
from app.services.payment_gateways import test_gateway
from app.services.payment_gateways.test_gateway import TestGateway
from tests.pay_support import cart_order, india_gst, notices_of, pay, world  # noqa: F401

API = "/refunds"


def paid(world, user=None, total="1000", currency=None, **extra):
    """A paid cart order: 1000 + 18% GST = 1180 (or in another currency)."""
    india_gst(world)
    user = user or world.people.priya
    order = cart_order(world, user, total=total)
    payment = pay(world, user, order, currency=currency, **extra).json()
    assert payment["status"] == "succeeded", payment
    return order, payment


def ask(world, user, payment, **body):
    return world.as_user(user).post(API, json={"payment_id": payment["id"], **body})


def fresh(world, refund_id):
    world.db.expire_all()
    return world.db.get(Refund, UUID(refund_id))


def payment_row(world, payment_id):
    world.db.expire_all()
    return world.db.get(Payment, UUID(payment_id))


# --- a customer asks, the business decides ---------------------------------------------------------------------------------------------------------

def test_a_customer_asks_for_a_partial_refund_and_the_business_approves_it(world):
    order, payment = paid(world)
    priya, asha = world.people.priya, world.people.asha

    asked = ask(world, priya, payment, amount="590", reason="The session was cancelled")

    assert asked.status_code == 201
    body = asked.json()
    assert body["status"] == "requested" and body["status_label"] == "Requested" and body["refund_type"] == "partial" and body["amount"] == "590.00"
    assert body["destination"] == "original" and body["actions"] == [] and body["sla_due_at"] is not None and body["requested_by_role"] == "customer"
    [notice] = notices_of(world, "refund_requested")
    assert str(notice["user_ids"][0]) == asha["id"] and notice["metadata"]["reason"] == "The session was cancelled" and "590" in notice["message"]
    assert world.as_user(asha).get(f"{API}/{body['id']}").json()["actions"] == ["approve", "reject"]
    assert payment_row(world, payment["id"]).refunded_amount == 0                      # nothing moves until it is approved

    done = world.as_user(asha).post(f"{API}/{body['id']}/decision", json={"action": "approve"}).json()

    assert done["status"] == "completed" and done["completed_at"] and done["credit_note_id"] and done["tax_refunded"] == "90.00"
    row = payment_row(world, payment["id"])
    assert row.status == "partially_refunded" and row.refunded_amount == Decimal("590.0000")
    assert [n["category"] for n in world.notices if n["category"].startswith("refund_")] == ["refund_requested", "refund_approved", "refund_completed"]
    completed = notices_of(world, "refund_completed")[0]
    assert str(completed["user_ids"][0]) == priya["id"] and "original payment method" in completed["message"]
    paid_again = world.as_user(priya).get(f"/payments/{payment['id']}").json()
    assert paid_again["refunded_amount"] == "590.00" and paid_again["refundable_amount"] == "590.00" and paid_again["status"] == "partially_refunded"
    world.db.expire_all()
    assert world.db.get(Order, order.id).status == "confirmed"                         # only a full refund changes the order


def test_the_credit_note_and_the_tax_reversal_follow_the_refund(world):
    order, payment = paid(world)
    refund = ask(world, world.people.priya, payment, amount="590", reason="Cancelled").json()
    world.as_user(world.people.asha).post(f"{API}/{refund['id']}/decision", json={"action": "approve"})

    note = world.db.query(Invoice).filter_by(doc_type="credit_note").one()
    assert note.number.startswith("CN-") and note.total == Decimal("590.0000") and note.tax_total == Decimal("90.0000") and note.subtotal == Decimal("500.0000")
    assert str(note.refund_id) == refund["id"] and note.parent_invoice_id == world.db.query(Invoice).filter_by(doc_type="invoice").one().id
    assert [(r["name"], r["tax_amount"]) for r in [{"name": b["name"], "tax_amount": b["tax_amount"]} for b in note.tax_breakdown]] == [("CGST", "45.00"), ("SGST", "45.00")]
    reversed_tax = world.db.query(TaxTransaction).filter(TaxTransaction.refund_id.isnot(None)).all()
    assert sorted((t.name, t.tax_amount) for t in reversed_tax) == [("CGST", Decimal("-45.0000")), ("SGST", Decimal("-45.0000"))]
    net = sum((t.tax_amount for t in world.db.query(TaxTransaction).all()), Decimal(0))
    assert net == Decimal("90.0000")                                                   # 180 charged, 90 given back
    assert notices_of(world, "invoice_generated")[-1]["metadata"]["doc_type"] == "credit_note"


def test_a_full_refund_closes_the_payment_and_reverses_the_order(world):
    order, payment = paid(world)

    done = ask(world, world.people.asha, payment).json()                            # the business refunds everything left

    assert done["status"] == "completed" and done["refund_type"] == "full" and done["amount"] == "1180.00" and done["requested_by_role"] == "merchant"
    row = payment_row(world, payment["id"])
    assert row.status == "refunded" and row.refunded_amount == Decimal("1180.0000")
    world.db.expire_all()
    assert world.db.get(Order, order.id).status == "refunded"
    assert sum((t.tax_amount for t in world.db.query(TaxTransaction).all()), Decimal(0)) == 0
    shown = world.as_user(world.people.priya).get(f"/payments/{payment['id']}").json()
    assert shown["status"] == "refunded" and shown["refundable_amount"] == "0.00" and "refund" not in shown["actions"]
    assert ask(world, world.people.asha, payment).status_code == 409


def test_a_refund_in_a_foreign_currency_is_refunded_in_that_currency(world):
    order, payment = paid(world, currency="USD")                                       # 14.16 USD

    done = ask(world, world.people.asha, payment, amount="7.08").json()

    assert done["currency"] == "USD" and done["amount"] == "7.08" and done["tax_refunded"] == "1.08"
    assert payment_row(world, payment["id"]).refunded_amount == Decimal("7.0800")
    assert test_gateway.ORDERS[payment_row(world, payment["id"]).gateway_order_id]["refunded_minor"] == 708
    note = world.db.query(Invoice).filter_by(doc_type="credit_note").one()
    assert note.currency == "USD" and note.item_currency == "INR" and note.total == Decimal("7.0800")


def test_rejecting_needs_a_reason_the_customer_can_read(world):
    order, payment = paid(world)
    refund = ask(world, world.people.priya, payment, reason="Changed my mind").json()
    asha = world.people.asha

    assert world.as_user(asha).post(f"{API}/{refund['id']}/decision", json={"action": "reject"}).status_code == 422
    rejected = world.as_user(asha).post(f"{API}/{refund['id']}/decision", json={"action": "reject", "reason": "Outside the cancellation window"}).json()

    assert rejected["status"] == "rejected" and rejected["decision_reason"] == "Outside the cancellation window"
    seen = world.as_user(world.people.priya).get(f"{API}/{refund['id']}").json()
    assert seen["decision_reason"] == "Outside the cancellation window" and seen["status_label"] == "Rejected"
    [notice] = notices_of(world, "refund_rejected")
    assert notice["metadata"]["reason"] == "Outside the cancellation window" and str(notice["user_ids"][0]) == world.people.priya["id"]
    assert payment_row(world, payment["id"]).refunded_amount == 0
    assert world.as_user(asha).post(f"{API}/{refund['id']}/decision", json={"action": "approve"}).status_code == 409        # decided already
    assert ask(world, world.people.priya, payment, reason="Asking again").status_code == 201                              # a new request is allowed


def test_what_can_be_asked_back_is_limited_and_a_request_reserves_its_amount(world):
    order, payment = paid(world)
    priya = world.people.priya

    assert ask(world, priya, payment, amount="1181", reason="Too much").status_code == 422
    assert ask(world, priya, payment, amount="0", reason="Nothing").status_code == 422
    assert ask(world, priya, payment, amount="500").status_code == 422                                                      # a customer must say why
    first = ask(world, priya, payment, amount="1000", reason="Part one")
    assert first.status_code == 201
    second = ask(world, priya, payment, amount="200", reason="Part two")
    assert second.status_code == 422 and "180.00" in second.json()["detail"]                                                # only 180 is left unreserved
    assert ask(world, priya, payment, amount="180", reason="The rest").status_code == 201
    assert ask(world, priya, payment, reason="Anything").status_code == 409                                                 # nothing left to ask


def test_two_partial_refunds_make_a_full_one(world):
    order, payment = paid(world)
    ask(world, world.people.asha, payment, amount="400")
    ask(world, world.people.asha, payment, amount="780")

    row = payment_row(world, payment["id"])
    assert row.status == "refunded" and row.refunded_amount == Decimal("1180.0000")
    assert world.db.query(Invoice).filter_by(doc_type="credit_note").count() == 2
    world.db.expire_all()
    assert world.db.get(Order, order.id).status == "refunded"
    assert sum((t.tax_amount for t in world.db.query(TaxTransaction).all()), Decimal(0)) == 0


def test_only_a_paid_payment_can_be_refunded(world):
    india_gst(world)
    priya = world.people.priya
    unpaid = pay(world, priya, cart_order(world, priya), token=None).json()["payment"]
    failed = pay(world, priya, cart_order(world, priya), token="tok_fail").json()

    assert ask(world, priya, unpaid, reason="Please").status_code == 409
    assert ask(world, priya, failed, reason="Please").status_code == 409


# --- who may do what ----------------------------------------------------------------------------------------------------------------------------------------

def test_only_the_business_and_the_platform_decide_and_nobody_sees_other_peoples_refunds(world):
    order, payment = paid(world)
    refund = ask(world, world.people.priya, payment, reason="Please").json()

    assert world.as_user(world.people.priya).post(f"{API}/{refund['id']}/decision", json={"action": "approve"}).status_code == 403
    assert world.as_user(world.people.bela).post(f"{API}/{refund['id']}/decision", json={"action": "approve"}).status_code == 404
    assert world.as_user(world.people.ravi).get(f"{API}/{refund['id']}").status_code == 404
    assert world.as_user(world.people.bela).get(f"{API}/{refund['id']}").status_code == 404
    assert ask(world, world.people.ravi, payment, reason="Not mine").status_code == 404
    assert ask(world, world.people.bela, payment).status_code == 404
    assert world.as_user(None).post(API, json={"payment_id": payment["id"]}).status_code == 401
    assert world.as_user(world.people.root).post(f"{API}/{refund['id']}/decision", json={"action": "approve"}).json()["status"] == "completed"


def test_a_customer_cannot_choose_where_the_money_goes(world):
    order, payment = paid(world)

    assert ask(world, world.people.priya, payment, reason="Please", destination="bank_transfer").status_code == 403
    assert ask(world, world.people.asha, payment, destination="wallet").status_code == 422


# --- where the money goes --------------------------------------------------------------------------------------------------------------------------------------

def test_a_bank_transfer_refund_waits_for_finance_to_record_the_payment(world):
    order, payment = paid(world)
    asha, root = world.people.asha, world.people.root

    refund = ask(world, asha, payment, amount="500", destination="bank_transfer").json()

    assert refund["status"] == "processing" and refund["actions"] == []                                                       # a business cannot complete it
    assert test_gateway.ORDERS[payment_row(world, payment["id"]).gateway_order_id]["refunded_minor"] == 0                      # the gateway was not used
    assert world.as_user(asha).post(f"{API}/{refund['id']}/complete-manual", json={"bank_reference": "NEFT-1"}).status_code == 403
    assert world.as_user(root).get(f"{API}/{refund['id']}").json()["actions"] == ["complete_manual"]
    done = world.as_user(root).post(f"{API}/{refund['id']}/complete-manual", json={"bank_reference": "NEFT-99231"}).json()
    assert done["status"] == "completed" and done["bank_reference"] == "NEFT-99231" and done["credit_note_id"]
    assert payment_row(world, payment["id"]).refunded_amount == Decimal("500.0000")
    assert "your bank account" in notices_of(world, "refund_completed")[0]["message"]
    assert world.as_user(root).post(f"{API}/{refund['id']}/complete-manual", json={"bank_reference": "NEFT-2"}).status_code == 409


def test_a_payment_too_old_for_the_gateway_must_go_to_a_bank(world):
    order, payment = paid(world)
    row = payment_row(world, payment["id"])
    row.captured_at = datetime.utcnow() - timedelta(days=200)
    world.db.commit()

    old = ask(world, world.people.asha, payment)

    assert old.status_code == 409 and "bank_transfer" in old.json()["detail"]
    assert ask(world, world.people.asha, payment, destination="bank_transfer").status_code == 201


# --- failure, retry and idempotency ------------------------------------------------------------------------------------------------------------------------------

def test_a_refund_the_gateway_refuses_fails_with_the_reason_and_can_be_retried(world):
    order, payment = paid(world)
    asha = world.people.asha
    TestGateway.fail_next_refund = "Insufficient balance in the merchant account"

    failed = ask(world, asha, payment, amount="500").json()

    assert failed["status"] == "failed" and failed["failure_reason"] == "Insufficient balance in the merchant account" and failed["actions"] == ["retry"]
    assert payment_row(world, payment["id"]).refunded_amount == 0 and world.db.query(Invoice).filter_by(doc_type="credit_note").count() == 0
    retried = world.as_user(asha).post(f"{API}/{failed['id']}/retry").json()
    assert retried["status"] == "completed" and retried["failure_reason"] is None
    assert payment_row(world, payment["id"]).refunded_amount == Decimal("500.0000")
    assert world.as_user(asha).post(f"{API}/{failed['id']}/retry").status_code == 409                       # completed refunds are not sent again
    assert world.as_user(world.people.priya).post(f"{API}/{failed['id']}/retry").status_code == 403


def test_sending_a_refund_again_never_refunds_twice(world):
    order, payment = paid(world)
    done = ask(world, world.people.asha, payment, amount="300").json()
    refund = fresh(world, done["id"])

    refund_service.process(world.db, refund)
    refund_service.complete(world.db, refund, world.db.get(Payment, refund.payment_id))

    assert payment_row(world, payment["id"]).refunded_amount == Decimal("300.0000")
    assert test_gateway.ORDERS[payment_row(world, payment["id"]).gateway_order_id]["refunded_minor"] == 30000
    assert world.db.query(Invoice).filter_by(doc_type="credit_note").count() == 1


# --- the gateway's refund webhooks ---------------------------------------------------------------------------------------------------------------------------------

def webhook(world, body):
    raw = json.dumps(body).encode()
    return world.as_user(None).post("/payments/webhook/test", content=raw, headers={"X-Test-Signature": test_gateway.sign(raw), "Content-Type": "application/json"})


def processing_refund(world, payment):
    row = payment_row(world, payment["id"])
    refund = Refund(payment_id=row.id, tenant_id=row.tenant_id, requested_by_role="merchant", amount=Decimal("400"), currency="INR", refund_type="partial", status="processing",
                    destination="original", gateway_refund_id="test_refund_async", decided_by="x", approved_at=datetime.utcnow(), sla_due_at=datetime.utcnow() + timedelta(hours=72))
    world.db.add(refund)
    world.db.commit()
    return refund


def test_a_refund_webhook_completes_a_refund_the_gateway_was_still_processing(world):
    order, payment = paid(world)
    refund = processing_refund(world, payment)

    resp = webhook(world, {"id": "evt_r1", "type": "refund.succeeded", "data": {"refund_id": "test_refund_async", "payment_id": payment_row(world, payment["id"]).gateway_payment_id}})
    again = webhook(world, {"id": "evt_r1", "type": "refund.succeeded", "data": {"refund_id": "test_refund_async"}})

    assert resp.json()["processed"] == 1 and again.json()["duplicates"] == 1
    assert fresh(world, str(refund.id)).status == "completed"
    assert payment_row(world, payment["id"]).refunded_amount == Decimal("400.0000") and payment_row(world, payment["id"]).status == "partially_refunded"
    assert world.db.query(Invoice).filter_by(doc_type="credit_note").count() == 1


def test_a_failed_refund_webhook_marks_the_refund_failed(world):
    order, payment = paid(world)
    refund = processing_refund(world, payment)

    webhook(world, {"id": "evt_r2", "type": "refund.failed", "data": {"refund_id": "test_refund_async", "failure_message": "Account closed"}})

    done = fresh(world, str(refund.id))
    assert done.status == "failed" and done.failure_reason == "Account closed" and payment_row(world, payment["id"]).refunded_amount == 0


# --- the target time ---------------------------------------------------------------------------------------------------------------------------------------------------

def test_a_refund_past_its_target_time_is_escalated_once(world):
    order, payment = paid(world)
    refund = ask(world, world.people.priya, payment, amount="100", reason="Please").json()
    row = fresh(world, refund["id"])
    row.sla_due_at = datetime.utcnow() - timedelta(hours=1)
    world.db.commit()

    shown = world.as_user(world.people.asha).get(f"{API}/{refund['id']}").json()
    assert shown["is_overdue"] is True
    assert world.as_user(world.people.asha).get(API, params={"overdue": "true"}).json()["total"] == 1
    assert refund_service.escalate_overdue(world.db) == 1
    assert refund_service.escalate_overdue(world.db) == 0

    [notice] = notices_of(world, "refund_escalated")
    assert {str(u) for u in notice["user_ids"]} == {world.people.root["id"], world.people.root2["id"]}
    assert notice["metadata"]["refund_id"] == refund["id"] and "overdue" in notice["title"].lower()
    assert fresh(world, refund["id"]).escalated_at is not None
    assert world.db.query(FinanceAuditLog).filter_by(action="refund_escalated").count() == 1


def test_approval_restarts_the_clock_and_a_completed_refund_is_never_overdue(world):
    order, payment = paid(world)
    refund = ask(world, world.people.priya, payment, amount="100", reason="Please").json()
    row = fresh(world, refund["id"])
    row.sla_due_at = datetime.utcnow() - timedelta(hours=1)
    world.db.commit()

    done = world.as_user(world.people.asha).post(f"{API}/{refund['id']}/decision", json={"action": "approve"}).json()

    assert done["is_overdue"] is False and done["status"] == "completed"
    assert refund_service.escalate_overdue(world.db) == 0
    assert datetime.fromisoformat(done["sla_due_at"]) > datetime.utcnow()


def test_stuck_refunds_are_sent_again_by_the_job(world):
    order, payment = paid(world)
    refund = processing_refund(world, payment)
    refund.gateway_refund_id = None
    refund.updated_at = datetime.utcnow() - timedelta(hours=1)
    world.db.commit()

    assert refund_service.retry_stuck(world.db) == 1
    assert fresh(world, str(refund.id)).status == "completed"


# --- history -------------------------------------------------------------------------------------------------------------------------------------------------------------------

def test_refund_history_is_scoped_and_filterable(world):
    order, payment = paid(world)
    other_order = cart_order(world, world.people.ravi, tenant=world.people.bela["tenant_id"] and __import__("uuid").UUID(world.people.bela["tenant_id"]))
    mine = ask(world, world.people.priya, payment, amount="100", reason="One").json()
    ask(world, world.people.asha, payment, amount="200")

    assert world.as_user(world.people.priya).get(API).json()["total"] == 2
    assert world.as_user(world.people.ravi).get(API).json()["total"] == 0
    assert world.as_user(world.people.asha).get(API).json()["total"] == 2
    assert world.as_user(world.people.bela).get(API).json()["total"] == 0
    assert world.as_user(world.people.root).get(API, params={"status": "requested"}).json()["total"] == 1
    assert world.as_user(world.people.root).get(API, params={"payment_id": payment["id"]}).json()["total"] == 2
    assert world.as_user(world.people.root).get(API, params={"status": "completed"}).json()["items"][0]["amount"] == "200.00"
    assert mine["id"] and other_order is not None


def test_every_refund_decision_is_audited(world):
    order, payment = paid(world)
    refund = ask(world, world.people.priya, payment, amount="100", reason="Please").json()
    world.as_user(world.people.asha).post(f"{API}/{refund['id']}/decision", json={"action": "approve"})

    actions = [e.action for e in world.db.query(FinanceAuditLog).filter_by(entity_type="refund").order_by(FinanceAuditLog.created_at).all()]
    assert actions == ["refund_approved", "refund_completed"]
    entry = world.db.query(FinanceAuditLog).filter_by(action="refund_approved").one()
    assert entry.actor_id == world.people.asha["id"] and entry.actor_role == "merchant"
