from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Body, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.core.payment_access import PayActor, get_pay_actor, require_super_admin
from app.db.database import get_db
from app.schemas.currency_schema import (
    ConversionResponse, CurrencyCreate, CurrencyListResponse, CurrencyResponse, CurrencyUpdate, ManualRate, PreferenceBody,
    PreferenceResponse, RateHistoryEntry, RateHistoryResponse, RateListResponse, RateResponse, RefreshResult,
)
from app.services import currency_service as svc

router = APIRouter(tags=["Currency"])


def _currency(row) -> CurrencyResponse:
    out = CurrencyResponse.model_validate(row)
    out.is_base = row.code == svc.base_currency()
    return out


def _rate(row) -> RateResponse:
    return RateResponse(base_currency=row.base_currency, quote_currency=row.quote_currency, rate=row.rate, source=row.source,
                        pinned=bool(row.pinned), updated_at=row.created_at)


@router.get("", response_model=CurrencyListResponse, summary="Supported currencies (public)",
            description="The currencies a customer can see prices and pay in, with their decimals and limits (FR-CUR-01, FR-CUR-10, FR-CUR-11).")
def get_supported_currencies(db: Session = Depends(get_db)):
    rows = svc.list_currencies(db)
    return CurrencyListResponse(base_currency=svc.base_currency(), items=[_currency(r) for r in rows], total=len(rows))


@router.get("/admin/all", response_model=CurrencyListResponse, summary="All currencies, including switched-off ones (Super Admin)")
def list_all_currencies(db: Session = Depends(get_db), _a: PayActor = Depends(require_super_admin)):
    rows = svc.list_currencies(db, include_inactive=True)
    return CurrencyListResponse(base_currency=svc.base_currency(), items=[_currency(r) for r in rows], total=len(rows))


@router.post("", response_model=CurrencyResponse, status_code=status.HTTP_201_CREATED, summary="Add a currency (Super Admin)")
def add_currency(payload: CurrencyCreate = Body(...), db: Session = Depends(get_db), actor: PayActor = Depends(require_super_admin)):
    return _currency(svc.create_currency(db, actor, payload))


@router.get("/base", summary="The base (accounting) currency (public)", description="FR-CUR-02.")
def get_base_currency(db: Session = Depends(get_db)):
    code = svc.base_currency()
    row = svc.get_currency(db, code)
    return {"base_currency": code, "decimals": svc.decimals_for(db, code), "name": row.name if row else None}


@router.get("/rates", response_model=RateListResponse, summary="Current exchange rates (public)",
            description="One unit of the base currency in each active currency (FR-CUR-05). `?quote=USD` returns just that one.")
def get_exchange_rates(quote: str | None = Query(None, min_length=3, max_length=3), db: Session = Depends(get_db)):
    rows = svc.current_rates(db)
    if quote:
        rows = [r for r in rows if r.quote_currency == quote.upper()]
    return RateListResponse(base_currency=svc.base_currency(), items=[_rate(r) for r in rows], total=len(rows))


@router.post("/rates", response_model=RateResponse, status_code=status.HTTP_201_CREATED, summary="Set an exchange rate by hand (Super Admin)",
             description="FR-CUR-07. The change is added to the history and the audit log. A `pinned` rate is not replaced by the automatic refresh.")
def update_exchange_rate(payload: ManualRate = Body(...), db: Session = Depends(get_db), actor: PayActor = Depends(require_super_admin)):
    return _rate(svc.set_manual_rate(db, actor, payload.quote_currency, payload.rate, pinned=payload.pinned, note=payload.note))


@router.post("/rates/refresh", response_model=RefreshResult, summary="Fetch the latest rates now (Super Admin)",
             description="FR-CUR-06. The same job also runs on a schedule. Pinned manual rates are skipped.")
def refresh_exchange_rates(db: Session = Depends(get_db), actor: PayActor = Depends(require_super_admin)):
    return svc.refresh_rates(db, actor)


@router.get("/rates/history", response_model=RateHistoryResponse, summary="Exchange rate history (Super Admin)", description="FR-CUR-08 and the exchange rate report.")
def exchange_rate_history(
    quote: str | None = Query(None, min_length=3, max_length=3), date_from: datetime | None = Query(None), date_to: datetime | None = Query(None),
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500), db: Session = Depends(get_db), _a: PayActor = Depends(require_super_admin),
):
    rows, total = svc.rate_history(db, quote=quote, date_from=date_from, date_to=date_to, page=page, page_size=page_size)
    return RateHistoryResponse(items=[RateHistoryEntry.model_validate(r, from_attributes=True) for r in rows], total=total)


@router.get("/convert", response_model=ConversionResponse, summary="Convert an amount (public)", description="FR-CUR-04. Rounded to the target currency's decimals.")
def convert_currency(
    amount: Decimal = Query(..., ge=0), from_currency: str = Query(..., alias="from", min_length=3, max_length=3),
    to_currency: str = Query(..., alias="to", min_length=3, max_length=3), db: Session = Depends(get_db),
):
    svc.require_active_currency(db, from_currency)
    svc.require_active_currency(db, to_currency)
    converted, rate, _ = svc.convert(db, amount, from_currency, to_currency)
    return ConversionResponse(amount=amount, from_currency=from_currency.upper(), to_currency=to_currency.upper(), rate=rate, converted=converted,
                              rate_updated_at=svc.rate_updated_at(db, from_currency, to_currency))


@router.get("/preference", response_model=PreferenceResponse, summary="My preferred currency", description="FR-CUR-03.")
def get_preferred_currency(db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor)):
    code, is_default = svc.get_preference(db, actor.user_uuid)
    return PreferenceResponse(currency=code, is_default=is_default)


@router.put("/preference", response_model=PreferenceResponse, summary="Save my preferred currency")
def update_preferred_currency(payload: PreferenceBody = Body(...), db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor)):
    return PreferenceResponse(currency=svc.set_preference(db, actor.user_uuid, payload.currency), is_default=False)


@router.patch("/{code}", response_model=CurrencyResponse, summary="Change a currency (Super Admin)",
              description="Switch it on or off, rename it, or set its smallest and largest single payment. The number of decimals is fixed once created.")
def change_currency(code: str = Path(..., min_length=3, max_length=3), payload: CurrencyUpdate = Body(...), db: Session = Depends(get_db),
                    actor: PayActor = Depends(require_super_admin)):
    return _currency(svc.update_currency(db, actor, code, payload))
