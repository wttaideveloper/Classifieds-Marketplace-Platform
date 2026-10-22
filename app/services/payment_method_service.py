"""A customer's saved payment methods (FR-PAY-04).

Only what the gateway hands back is kept: its token, the card brand, the last four digits and the expiry. A card number, CVV or
anything else that would put this system in PCI scope is never accepted or stored.
"""
from datetime import date
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.payment_model import SavedPaymentMethod
from app.services.payment_gateways import base as gw
from app.services.payment_gateways import get_gateway

FORBIDDEN_FIELDS = {"card_number", "number", "cvv", "cvc", "pan", "card"}


def _err(status: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail=detail)


def list_methods(db: Session, user_id: UUID) -> list[SavedPaymentMethod]:
    return db.query(SavedPaymentMethod).filter(SavedPaymentMethod.user_id == user_id, SavedPaymentMethod.is_active.is_(True)).order_by(
        SavedPaymentMethod.is_default.desc(), SavedPaymentMethod.created_at.desc()).all()


def is_expired(method: SavedPaymentMethod, today: date | None = None) -> bool:
    today = today or date.today()
    return bool(method.exp_year and method.exp_month and (method.exp_year, method.exp_month) < (today.year, today.month))


def save_method(db: Session, actor, body) -> SavedPaymentMethod:
    raw = body.model_dump()
    leaked = FORBIDDEN_FIELDS & {k.lower() for k in raw}
    if leaked:
        raise _err(422, "Never send card details. Send only the token the payment gateway's SDK returned")
    customer = gw.GatewayCustomer(user_id=actor.id, email=actor.email, name=actor.name,
                                  gateway_customer_id=_existing_customer(db, actor.user_uuid))
    try:
        info = get_gateway().save_method(customer, raw)
    except gw.GatewayError as exc:
        raise _err(502 if exc.retryable else 422, exc.message)
    if db.query(SavedPaymentMethod).filter(SavedPaymentMethod.user_id == actor.user_uuid, SavedPaymentMethod.token == info.token, SavedPaymentMethod.is_active.is_(True)).first():
        raise _err(409, "This payment method is already saved")
    has_any = bool(list_methods(db, actor.user_uuid))
    row = SavedPaymentMethod(user_id=actor.user_uuid, provider=settings.PAYMENT_GATEWAY, gateway_customer_id=info.gateway_customer_id, token=info.token, brand=info.brand,
                             last4=info.last4, exp_month=info.exp_month, exp_year=info.exp_year, holder_name=body.holder_name, is_default=body.set_default or not has_any)
    if row.is_default:
        db.query(SavedPaymentMethod).filter(SavedPaymentMethod.user_id == actor.user_uuid).update({SavedPaymentMethod.is_default: False})
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _existing_customer(db: Session, user_id: UUID) -> str | None:
    row = db.query(SavedPaymentMethod).filter(SavedPaymentMethod.user_id == user_id, SavedPaymentMethod.provider == settings.PAYMENT_GATEWAY,
                                              SavedPaymentMethod.gateway_customer_id.isnot(None)).first()
    return row.gateway_customer_id if row else None


def _mine(db: Session, actor, method_id: UUID) -> SavedPaymentMethod:
    row = db.get(SavedPaymentMethod, method_id)
    if row is None or not row.is_active or row.user_id != actor.user_uuid:
        raise _err(404, "Saved payment method not found")
    return row


def set_default(db: Session, actor, method_id: UUID) -> SavedPaymentMethod:
    row = _mine(db, actor, method_id)
    db.query(SavedPaymentMethod).filter(SavedPaymentMethod.user_id == actor.user_uuid).update({SavedPaymentMethod.is_default: False})
    row.is_default = True
    db.commit()
    db.refresh(row)
    return row


def delete_method(db: Session, actor, method_id: UUID) -> None:
    row = _mine(db, actor, method_id)
    try:
        get_gateway(row.provider).remove_method(row.token, row.gateway_customer_id)
    except gw.GatewayError:
        pass                      # it is removed here either way; the gateway copy is only a token
    row.is_active, was_default = False, row.is_default
    row.is_default = False
    if was_default:
        nxt = db.query(SavedPaymentMethod).filter(SavedPaymentMethod.user_id == actor.user_uuid, SavedPaymentMethod.is_active.is_(True), SavedPaymentMethod.id != row.id).order_by(
            SavedPaymentMethod.created_at.desc()).first()
        if nxt is not None:
            nxt.is_default = True
    db.commit()
