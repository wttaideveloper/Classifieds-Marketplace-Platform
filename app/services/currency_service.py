"""Supported currencies, exchange rates and conversion.

Rates are stored against the organisation's base currency (1 base = `rate` of the quote currency) and only ever added, so the
table is its own history. Converting A to B goes through the base currency: B_rate / A_rate. A payment locks the rates it
used in `Payment.rate_snapshot`, so a later change never alters what was charged.
"""
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

import requests
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.payment_model import Currency, CurrencyPreference, ExchangeRate
from app.services import finance_audit
from app.utils.money import default_decimals, quantize, to_decimal

logger = logging.getLogger(__name__)

RATE_PRECISION = Decimal("0.0000000001")
CURRENCY_FIELDS = ("name", "symbol", "decimals", "is_active", "min_amount", "max_amount", "sort_order")


class RatesUnavailable(HTTPException):
    def __init__(self, detail: str):
        super().__init__(status_code=503, detail=detail)


def base_currency() -> str:
    return (settings.PAYMENT_BASE_CURRENCY or "INR").strip().upper()


def _iso_name(code: str) -> str | None:
    try:
        import pycountry

        row = pycountry.currencies.get(alpha_3=code)
        return row.name if row else None
    except Exception:
        return None


# ---------------------------------------------------------------- currencies

def get_currency(db: Session, code: str) -> Currency | None:
    return db.get(Currency, (code or "").strip().upper())


def decimals_for(db: Session, code: str) -> int:
    row = get_currency(db, code)
    return row.decimals if row is not None else default_decimals(code)


def require_active_currency(db: Session, code: str) -> Currency:
    row = get_currency(db, code)
    if row is None or not row.is_active:
        raise HTTPException(status_code=422, detail=f"The currency {str(code).upper()} is not supported")
    return row


def list_currencies(db: Session, *, include_inactive: bool = False) -> list[Currency]:
    query = db.query(Currency)
    if not include_inactive:
        query = query.filter(Currency.is_active.is_(True))
    return query.order_by(Currency.sort_order, Currency.code).all()


def create_currency(db: Session, actor, payload) -> Currency:
    if get_currency(db, payload.code):
        raise HTTPException(status_code=409, detail=f"The currency {payload.code} already exists")
    name = payload.name or _iso_name(payload.code)
    if not name:
        raise HTTPException(status_code=422, detail=f"{payload.code} is not a known currency code. Send a name for it")
    _check_limits(payload.min_amount, payload.max_amount)
    row = Currency(
        code=payload.code, name=name, symbol=payload.symbol, decimals=payload.decimals if payload.decimals is not None else default_decimals(payload.code),
        is_active=payload.is_active, min_amount=payload.min_amount, max_amount=payload.max_amount,
        sort_order=(db.query(func.max(Currency.sort_order)).scalar() or 0) + 1,
    )
    db.add(row)
    finance_audit.record(db, actor, "currency_created", "currency", row.code, after=finance_audit.snapshot(row, *CURRENCY_FIELDS))
    db.commit()
    db.refresh(row)
    return row


def _check_limits(minimum, maximum) -> None:
    if minimum is not None and maximum is not None and minimum > maximum:
        raise HTTPException(status_code=422, detail="The minimum amount cannot be more than the maximum")


def update_currency(db: Session, actor, code: str, payload) -> Currency:
    row = get_currency(db, code)
    if row is None:
        raise HTTPException(status_code=404, detail="Currency not found")
    before = finance_audit.snapshot(row, *CURRENCY_FIELDS)
    data = payload.model_dump(exclude_unset=True)
    clear = data.pop("clear_limits", False)
    changes = {k: v for k, v in data.items() if k in ("min_amount", "max_amount", "symbol") or v is not None}
    if clear:
        changes["min_amount"] = changes["max_amount"] = None
    if row.code == base_currency() and changes.get("is_active") is False:
        raise HTTPException(status_code=409, detail="The base currency cannot be switched off")
    _check_limits(changes.get("min_amount", row.min_amount), changes.get("max_amount", row.max_amount))
    for key, value in changes.items():
        setattr(row, key, value)
    finance_audit.record(db, actor, "currency_updated", "currency", row.code, before=before, after=finance_audit.snapshot(row, *CURRENCY_FIELDS))
    db.commit()
    db.refresh(row)
    return row


def check_amount_limits(db: Session, code: str, amount: Decimal) -> None:
    """The currency's smallest and largest single payment (FR-CUR-11)."""
    row = get_currency(db, code)
    if row is None:
        return
    if row.min_amount is not None and amount < row.min_amount:
        raise HTTPException(status_code=422, detail=f"The smallest payment in {row.code} is {row.min_amount:f}")
    if row.max_amount is not None and amount > row.max_amount:
        raise HTTPException(status_code=422, detail=f"The largest payment in {row.code} is {row.max_amount:f}")


# ---------------------------------------------------------------- rates

def latest_rate_row(db: Session, quote: str, base: str | None = None) -> ExchangeRate | None:
    return (
        db.query(ExchangeRate)
        .filter(ExchangeRate.base_currency == (base or base_currency()), ExchangeRate.quote_currency == quote.upper())
        .order_by(ExchangeRate.created_at.desc(), ExchangeRate.id)
        .first()
    )


def current_rates(db: Session) -> list[ExchangeRate]:
    """The newest rate of every quote currency, for the active currencies."""
    base = base_currency()
    rows = []
    for currency in list_currencies(db):
        if currency.code == base:
            continue
        row = latest_rate_row(db, currency.code, base)
        if row is not None:
            rows.append(row)
    return rows


def _units_per_base(db: Session, code: str, *, enforce_age: bool, now: datetime) -> tuple[Decimal, ExchangeRate | None]:
    code = code.upper()
    if code == base_currency():
        return Decimal(1), None
    row = latest_rate_row(db, code)
    if row is None:
        raise RatesUnavailable(f"There is no exchange rate for {code} yet")
    if enforce_age and not row.pinned and row.created_at < now - timedelta(hours=settings.EXCHANGE_RATE_MAX_AGE_HOURS):
        raise RatesUnavailable(f"The exchange rate for {code} is out of date. Try again shortly")
    return to_decimal(row.rate), row


def rate_between(db: Session, from_code: str, to_code: str, *, enforce_age: bool = False, now: datetime | None = None) -> tuple[Decimal, dict]:
    """1 unit of `from_code` = (rate) units of `to_code`, with the rate rows used (for the payment's snapshot)."""
    now = now or datetime.utcnow()
    from_code, to_code = from_code.upper(), to_code.upper()
    if from_code == to_code:
        return Decimal(1), {"from": from_code, "to": to_code, "rate": "1", "rows": []}
    from_units, from_row = _units_per_base(db, from_code, enforce_age=enforce_age, now=now)
    to_units, to_row = _units_per_base(db, to_code, enforce_age=enforce_age, now=now)
    rate = (to_units / from_units).quantize(RATE_PRECISION)
    rows = [{"currency": r.quote_currency, "rate": format(r.rate, "f"), "id": str(r.id), "source": r.source, "at": r.created_at.isoformat()}
            for r in (from_row, to_row) if r is not None]
    return rate, {"from": from_code, "to": to_code, "rate": format(rate, "f"), "base": base_currency(), "rows": rows}


def convert(db: Session, amount: Decimal, from_code: str, to_code: str, *, enforce_age: bool = False, mode: str = "half_up") -> tuple[Decimal, Decimal, dict]:
    """(converted amount rounded to the target currency, rate used, snapshot)."""
    rate, snapshot = rate_between(db, from_code, to_code, enforce_age=enforce_age)
    return quantize(to_decimal(amount) * rate, decimals_for(db, to_code), mode), rate, snapshot


def rate_updated_at(db: Session, from_code: str, to_code: str) -> datetime | None:
    stamps = [latest_rate_row(db, c).created_at for c in (from_code, to_code) if c.upper() != base_currency() and latest_rate_row(db, c)]
    return min(stamps) if stamps else None


def set_manual_rate(db: Session, actor, quote: str, rate: Decimal, *, pinned: bool = False, note: str | None = None) -> ExchangeRate:
    quote = quote.upper()
    if quote == base_currency():
        raise HTTPException(status_code=422, detail="The base currency always has the rate 1")
    require_active_currency(db, quote)
    previous = latest_rate_row(db, quote)
    row = ExchangeRate(base_currency=base_currency(), quote_currency=quote, rate=to_decimal(rate).quantize(RATE_PRECISION),
                       source="manual", provider="admin", pinned=pinned, created_by=actor.id, note=note)
    if row.rate <= 0:
        raise HTTPException(status_code=422, detail="The rate must be more than zero")
    db.add(row)
    finance_audit.record(
        db, actor, "exchange_rate_set", "exchange_rate", f"{row.base_currency}/{quote}",
        before={"rate": previous.rate, "source": previous.source} if previous else None,
        after={"rate": row.rate, "pinned": pinned}, reason=note,
    )
    db.commit()
    db.refresh(row)
    return row


def fetch_provider_rates(base: str, quotes: list[str]) -> dict[str, Decimal]:
    """The reference rates from the rate service. Raises on any failure; tests replace this function."""
    response = requests.get(settings.EXCHANGE_RATE_API_URL, params={"from": base, "to": ",".join(quotes)}, timeout=settings.PAYMENT_GATEWAY_TIMEOUT_SECONDS)
    response.raise_for_status()
    rates = (response.json() or {}).get("rates") or {}
    return {code.upper(): to_decimal(value) for code, value in rates.items() if to_decimal(value, Decimal(0)) > 0}


def refresh_rates(db: Session, actor=None) -> dict:
    """Fetch the latest rates for every active currency and add them. A pinned manual rate is left alone."""
    base = base_currency()
    quotes = [c.code for c in list_currencies(db) if c.code != base]
    if not quotes:
        return {"base_currency": base, "updated": [], "skipped_pinned": [], "missing": [], "provider": "none"}
    try:
        fetched = fetch_provider_rates(base, quotes)
    except Exception as exc:
        logger.exception("exchange rate refresh failed")
        raise HTTPException(status_code=502, detail=f"The exchange rate service did not answer: {type(exc).__name__}")
    updated, pinned, missing = [], [], []
    for code in quotes:
        last = latest_rate_row(db, code)
        if last is not None and last.pinned and last.source == "manual":
            pinned.append(code)
            continue
        if code not in fetched:
            missing.append(code)
            continue
        db.add(ExchangeRate(base_currency=base, quote_currency=code, rate=fetched[code].quantize(RATE_PRECISION), source="auto",
                            provider="frankfurter", created_by=getattr(actor, "id", None)))
        updated.append(code)
    finance_audit.record(db, actor, "exchange_rates_refreshed", "exchange_rate", base, after={"updated": updated, "skipped_pinned": pinned, "missing": missing})
    db.commit()
    return {"base_currency": base, "updated": updated, "skipped_pinned": pinned, "missing": missing, "provider": "frankfurter"}


def rate_history(db: Session, *, quote: str | None = None, date_from: datetime | None = None, date_to: datetime | None = None,
                 page: int = 1, page_size: int = 50) -> tuple[list[ExchangeRate], int]:
    query = db.query(ExchangeRate)
    if quote:
        query = query.filter(ExchangeRate.quote_currency == quote.upper())
    if date_from:
        query = query.filter(ExchangeRate.created_at >= date_from)
    if date_to:
        query = query.filter(ExchangeRate.created_at <= date_to)
    total = query.count()
    rows = query.order_by(ExchangeRate.created_at.desc(), ExchangeRate.id).offset((page - 1) * page_size).limit(page_size).all()
    return rows, total


# ---------------------------------------------------------------- preference

def get_preference(db: Session, user_id: UUID) -> tuple[str, bool]:
    row = db.get(CurrencyPreference, user_id)
    if row is not None:
        return row.currency, False
    return base_currency(), True


def set_preference(db: Session, user_id: UUID, code: str) -> str:
    require_active_currency(db, code)
    row = db.get(CurrencyPreference, user_id)
    if row is None:
        db.add(CurrencyPreference(user_id=user_id, currency=code.upper()))
    else:
        row.currency = code.upper()
    db.commit()
    return code.upper()
