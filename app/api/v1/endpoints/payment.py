from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, Path, Query, Request, status
from sqlalchemy.orm import Session

from app.core.payment_access import PayActor, get_pay_actor, require_merchant_or_admin, require_super_admin
from app.db.database import get_db
from app.schemas.payment_schema import (
    CheckoutResponse, PaymentListResponse, PaymentRequest, PaymentResponse, QuoteResponse, RetryRequest, ReviewDecision, WebhookAck,
)
from app.services import payment_service, payment_view

router = APIRouter(tags=["Payments"])


@router.post("/quote", response_model=QuoteResponse, summary="Price of a payment before the customer confirms it",
             description="Shows the items, the tax per component, the exchange rate and the amount in the currency the customer will pay in. "
                         "Nothing is saved and no gateway is called (FR-PAY-01, usability: tax and currency are shown before payment).")
def quote_payment(payload: PaymentRequest = Body(...), db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor)):
    pricing, payable = payment_service.quote(db, actor, payload)
    return payment_view.quote_response(db, pricing, payable)


@router.post("", response_model=CheckoutResponse, status_code=status.HTTP_201_CREATED, summary="Create a payment",
             description="FR-PAY-01. Names what is bought (`cart_order`, `event_order`, or a `custom` payment a business asks a customer to make); the amount is read from the "
                         "order, never from the request. Returns the payment and `checkout`, what the gateway's SDK needs. Send an `Idempotency-Key` header so a repeated request "
                         "returns the same payment. A customer who comes back to an unpaid payment for the same order gets that payment again.")
def create_payment(request: Request, payload: PaymentRequest = Body(...), idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
                   db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor)):
    payment, checkout = payment_service.create_payment(db, actor, payload, idempotency_key, access_token=actor.token)
    return CheckoutResponse(payment=payment_view.payment_response(db, payment, actor), checkout=checkout)


@router.post("/webhook/{provider}", response_model=WebhookAck, summary="Gateway callback (webhook)",
             description="FR-PAY-13 and the Payment Webhook API. Called by the payment gateway, not by apps. The signature is checked before anything is read, "
                         "and each event id is handled once. Unknown payments are acknowledged so the gateway stops retrying.")
async def payment_webhook(provider: str, request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    return payment_service.handle_webhook(db, provider.lower(), dict(request.headers), raw)


@router.get("", response_model=PaymentListResponse, summary="Payment history",
            description="FR-PAY-03. A customer sees their own payments, a business the payments made to it, the platform all. Newest first.")
def payment_history(
    status_filter: str | None = Query(None, alias="status"), currency: str | None = Query(None, min_length=3, max_length=3), purchase_type: str | None = Query(None),
    purchase_id: str | None = Query(None), tenant_id: UUID | None = Query(None, description="Super Admin only"), customer_user_id: UUID | None = Query(None, description="Super Admin only"),
    needs_reconciliation: bool | None = Query(None, description="Business / platform: payments waiting to be checked with the gateway"),
    date_from: datetime | None = Query(None), date_to: datetime | None = Query(None), page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor),
):
    rows, total = payment_service.list_payments(db, actor, status=status_filter, currency=currency, purchase_type=purchase_type, purchase_id=purchase_id, tenant_id=tenant_id,
                                                customer_user_id=customer_user_id, date_from=date_from, date_to=date_to, needs_reconciliation=needs_reconciliation,
                                                page=page, page_size=page_size)
    return PaymentListResponse(items=[payment_view.payment_response(db, p, actor) for p in rows], total=total, page=page, page_size=page_size)


@router.get("/{payment_id}", response_model=PaymentResponse, summary="Payment details", description="The payment with its items, tax, exchange rate and what you can do with it now (`actions`).")
def get_payment(payment_id: UUID = Path(...), db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor)):
    return payment_view.payment_response(db, payment_service.get_visible(db, actor, payment_id), actor)


@router.get("/{payment_id}/checkout", response_model=CheckoutResponse, summary="Open the checkout of an unpaid payment",
            description="Gives the person who is paying what the gateway's SDK needs, for a payment that is waiting (for example one a business asked them to make).")
def get_checkout(payment_id: UUID, db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor)):
    payment, params = payment_service.get_checkout(db, actor, payment_id)
    return CheckoutResponse(payment=payment_view.payment_response(db, payment, actor), checkout=params)


@router.post("/{payment_id}/verify", response_model=PaymentResponse, summary="Verify a payment with the gateway",
             description="FR-PAY-02, FR-PAY-11. Send what the browser / app got back from the gateway (Razorpay: razorpay_order_id, razorpay_payment_id, razorpay_signature; "
                         "Stripe: payment_intent_id or nothing; test gateway: test_token). The signature is checked and the gateway's own answer decides. Safe to call again. "
                         "`requires_action` means 3-D Secure / OTP is still to be done: call again after it. If the gateway does not answer, the payment becomes "
                         "`processing` and is checked automatically.")
def verify_payment(payment_id: UUID, client_data: dict = Body(default_factory=dict), db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor)):
    return payment_view.payment_response(db, payment_service.verify_payment(db, actor, payment_id, client_data, access_token=actor.token), actor)


@router.post("/{payment_id}/retry", response_model=CheckoutResponse, status_code=status.HTTP_201_CREATED, summary="Retry a failed payment",
             description="FR-PAY-05. Starts a new attempt for the same order; the failed one stays in the history. The price is worked out again with the current rate. "
                         "A limited number of attempts is allowed per order.")
def retry_payment(payment_id: UUID, payload: RetryRequest = Body(default_factory=RetryRequest), db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor)):
    payment, checkout = payment_service.retry_payment(db, actor, payment_id, payload, access_token=actor.token)
    return CheckoutResponse(payment=payment_view.payment_response(db, payment, actor), checkout=checkout)


@router.post("/{payment_id}/cancel", response_model=PaymentResponse, summary="Cancel a payment",
             description="Cancels a payment that has not taken money, or one that is authorized and not captured. A paid payment is refunded instead.")
def cancel_payment(payment_id: UUID, db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor)):
    return payment_view.payment_response(db, payment_service.cancel_payment(db, actor, payment_id, access_token=actor.token), actor)


@router.post("/{payment_id}/capture", response_model=PaymentResponse, summary="Capture an authorized payment (business / platform)",
             description="For a payment made with capture_method=manual: takes the held money. The invoice is issued then.")
def capture_payment(payment_id: UUID, db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin)):
    return payment_view.payment_response(db, payment_service.capture_payment(db, actor, payment_id, access_token=actor.token), actor)


@router.post("/{payment_id}/resolve", response_model=PaymentResponse, summary="Settle a payment held for review (Super Admin)",
             description="A risk hold: approve sends it to the gateway, reject fails it. Money that arrived late or in the wrong amount: approve accepts it as paid, reject refunds it.")
def resolve_payment(payment_id: UUID, payload: ReviewDecision = Body(...), db: Session = Depends(get_db), actor: PayActor = Depends(require_super_admin)):
    return payment_view.payment_response(db, payment_service.decide_review(db, actor, payment_id, payload.action, payload.note, access_token=actor.token), actor)


@router.get("/{payment_id}/transactions", summary="Gateway trail of a payment (business / platform)",
            description="Every exchange with the gateway and every state change, oldest first. Used to answer disputes.")
def payment_transactions(payment_id: UUID, db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin)):
    payment = payment_service.get_visible(db, actor, payment_id)
    return {"payment_id": str(payment.id), "items": [
        {"id": str(t.id), "kind": t.kind, "status": t.status, "gateway_ref": t.gateway_ref, "amount": t.amount, "currency": t.currency, "error": t.error,
         "detail": t.detail, "created_at": t.created_at} for t in payment_service.transactions_of(db, payment)]}
