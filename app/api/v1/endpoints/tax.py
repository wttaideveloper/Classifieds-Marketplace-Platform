from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.payment_access import PayActor, get_pay_actor, require_merchant_or_admin, require_super_admin
from app.db.database import get_db
from app.schemas.tax_schema import (
    ExemptionCreate, ExemptionDecision, ExemptionListResponse, ExemptionResponse, MerchantTaxProfileBody, MerchantTaxProfileResponse,
    TaxCalcRequest, TaxCalcResponse, TaxHistoryResponse, TaxRuleBody, TaxRuleChange, TaxRuleListResponse, TaxRuleResponse,
    TaxTransactionResponse, ValidateIdRequest, ValidateIdResponse,
)
from app.services import currency_service, finance_audit, payment_export
from app.services import tax_service as svc

router = APIRouter(tags=["Tax"])


def _rule(row) -> TaxRuleResponse:
    out = TaxRuleResponse.model_validate(row)
    out.is_current = svc.rule_is_current(row)
    return out


def _token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    return header.split(" ", 1)[1].strip() if header.lower().startswith("bearer ") else None


# ---------------------------------------------------------------- rules

@router.get("/rules", response_model=TaxRuleListResponse, summary="Tax rules",
            description="The rules in force now (FR-TAX-05, FR-TAX-06). `current_only=false` also lists scheduled and replaced versions.")
def get_tax_rules(
    country: str | None = Query(None, min_length=2, max_length=2), state: str | None = Query(None), tax_type: str | None = Query(None),
    current_only: bool = Query(True), db: Session = Depends(get_db), _a: PayActor = Depends(get_pay_actor),
):
    rows = svc.list_rules(db, country=country, state=state, tax_type=tax_type, current_only=current_only)
    return TaxRuleListResponse(items=[_rule(r) for r in rows], total=len(rows))


@router.post("/rules", response_model=TaxRuleResponse, status_code=status.HTTP_201_CREATED, summary="Create a tax rule (Super Admin)",
             description="Takes effect from `valid_from` (default now) with no release. The change is audited and the Super Admins are told.")
def create_tax_rule(request: Request, payload: TaxRuleBody = Body(...), db: Session = Depends(get_db), actor: PayActor = Depends(require_super_admin)):
    return _rule(svc.create_rule(db, actor, payload, _token(request)))


@router.get("/rules/{rule_id}/versions", response_model=TaxRuleListResponse, summary="Every version of a rule (Super Admin)")
def tax_rule_versions(rule_id: UUID, db: Session = Depends(get_db), _a: PayActor = Depends(require_super_admin)):
    rows = svc.rule_versions(db, rule_id)
    return TaxRuleListResponse(items=[_rule(r) for r in rows], total=len(rows))


@router.put("/rules/{rule_id}", response_model=TaxRuleResponse, summary="Change a tax rule (Super Admin)",
            description="Adds the next version, valid from `valid_from` (default now). The version it replaces keeps applying until then, "
                        "so earlier payments stay explainable. Send only what changes.")
def update_tax_rule(request: Request, rule_id: UUID, payload: TaxRuleChange = Body(...), db: Session = Depends(get_db), actor: PayActor = Depends(require_super_admin)):
    return _rule(svc.change_rule(db, actor, rule_id, payload, _token(request)))


@router.delete("/rules/{rule_id}", response_model=TaxRuleResponse, summary="Switch a tax rule off (Super Admin)")
def deactivate_tax_rule(request: Request, rule_id: UUID, reason: str | None = Query(None, max_length=500), db: Session = Depends(get_db),
                        actor: PayActor = Depends(require_super_admin)):
    return _rule(svc.deactivate_rule(db, actor, rule_id, reason, _token(request)))


# ---------------------------------------------------------------- calculate

@router.post("/calculate", response_model=TaxCalcResponse, summary="Calculate tax for a transaction",
             description="FR-TAX-01 to FR-TAX-07, FR-TAX-10, FR-TAX-11. Shows what the customer will be charged before they confirm: tax per line, the "
                         "breakdown by component (CGST / SGST / IGST / VAT ...), exemption and reverse charge.")
def calculate_tax(payload: TaxCalcRequest = Body(...), db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor)):
    currency_service.require_active_currency(db, payload.currency)
    items = [svc.TaxItemIn(unit_amount=i.amount, quantity=i.quantity, category=i.category, description=i.description) for i in payload.items]
    ctx = svc.build_context(
        db, currency=payload.currency, customer_country=payload.customer.country, customer_state=payload.customer.state,
        customer_tax_id=payload.customer.tax_id, customer_is_business=payload.customer.is_business, tenant_id=payload.merchant_tenant_id,
        user_id=actor.user_uuid, prices_include_tax=payload.prices_include_tax,
    )
    return svc.calculate(db, items, ctx)


# ---------------------------------------------------------------- history and reports

@router.get("/history", response_model=TaxHistoryResponse, summary="Tax history",
            description="FR-TAX-09. The tax of completed payments (refunds appear as negative rows). A customer sees their own, a merchant their business's, the platform all.")
def get_tax_history(
    tenant_id: UUID | None = Query(None, description="Super Admin only"), payment_id: UUID | None = Query(None), country: str | None = Query(None, min_length=2, max_length=2),
    tax_type: str | None = Query(None), date_from: datetime | None = Query(None), date_to: datetime | None = Query(None),
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500), db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor),
):
    rows, total = svc.tax_history(db, actor, tenant_id=tenant_id, payment_id=payment_id, country=country, tax_type=tax_type,
                                  date_from=date_from, date_to=date_to, page=page, page_size=page_size)
    return TaxHistoryResponse(items=[TaxTransactionResponse.model_validate(r) for r in rows], total=total, page=page, page_size=page_size)


@router.get("/reports", summary="Tax report", description="FR-TAX-08. Tax collected, net of refunds, grouped by tax_type, country, state, name, month or day. `format=csv` downloads it.")
def get_tax_report(
    group_by: Literal["tax_type", "country", "state", "name", "month", "day"] = Query("tax_type"), tenant_id: UUID | None = Query(None, description="Super Admin only"),
    date_from: datetime | None = Query(None), date_to: datetime | None = Query(None), format: Literal["json", "csv"] = Query("json"),
    db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin),
):
    report = svc.tax_report(db, actor, tenant_id=tenant_id, date_from=date_from, date_to=date_to, group_by=group_by)
    if format == "csv":
        rows = [{**r, "tax_amount_base": r["tax_amount_base"]} for r in report["rows"]]
        return payment_export.csv_response(rows, [group_by, "transactions", "taxable_amount", "tax_amount", "tax_amount_base", "reverse_charge_taxable", "exempt_taxable"], "tax-report.csv")
    return finance_audit.jsonable(report)


# ---------------------------------------------------------------- registrations and exemption certificates

@router.post("/validate-exemption", response_model=ValidateIdResponse, summary="Check a GSTIN / VAT number / exemption certificate",
             description="FR-TAX-12. Checks the format (and a GSTIN's check character). Nothing is saved and no tax changes: register the number with POST /tax/exemptions.")
def validate_tax_exemption(payload: ValidateIdRequest = Body(...), _a: PayActor = Depends(get_pay_actor)):
    check = svc.check_number(payload.id_type, payload.number, payload.country)
    return ValidateIdResponse(id_type=payload.id_type, number=check.normalized, valid=check.valid, checksum_ok=check.checksum_ok, state=check.state, message=check.message)


@router.post("/exemptions", response_model=ExemptionResponse, status_code=status.HTTP_201_CREATED, summary="Register my tax number or exemption certificate",
             description="Starts as `pending`. Once a Super Admin approves it, a business customer abroad is not charged on reverse-charge rules (GSTIN / VAT) "
                         "or a certificate holder pays no tax. The format is checked first (422 when wrong).")
def register_exemption(payload: ExemptionCreate = Body(...), db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor)):
    return svc.register_exemption(db, actor.user_uuid, payload)


@router.get("/exemptions", response_model=ExemptionListResponse, summary="My registered numbers (Super Admin: everyone's)")
def list_exemptions(
    status_filter: str | None = Query(None, alias="status"), user_id: UUID | None = Query(None, description="Super Admin only"),
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), db: Session = Depends(get_db), actor: PayActor = Depends(get_pay_actor),
):
    owner = user_id if actor.is_super_admin else actor.user_uuid
    rows, total = svc.list_exemptions(db, user_id=owner, status=status_filter, page=page, page_size=page_size)
    return ExemptionListResponse(items=[ExemptionResponse.model_validate(r) for r in rows], total=total)


@router.patch("/exemptions/{exemption_id}", response_model=ExemptionResponse, summary="Approve, reject or revoke a registration (Super Admin)",
              description="Rejecting or revoking needs a note.")
def decide_exemption(exemption_id: UUID, payload: ExemptionDecision = Body(...), db: Session = Depends(get_db), actor: PayActor = Depends(require_super_admin)):
    return svc.decide_exemption(db, actor, exemption_id, payload.action, payload.note)


# ---------------------------------------------------------------- merchant tax profile

def _profile_tenant(actor: PayActor, tenant_id: UUID | None) -> UUID:
    if actor.role == "merchant":
        if tenant_id not in (None, actor.tenant_id):
            raise HTTPException(status_code=403, detail="You can only use your own business's tax profile")
        return actor.tenant_id
    if tenant_id is None:
        raise HTTPException(status_code=422, detail="tenant_id is required")
    return tenant_id


@router.get("/merchant-profile", response_model=MerchantTaxProfileResponse, summary="A business's tax profile",
            description="Where the merchant supplies from and their tax registration. It decides same-state (CGST + SGST) or cross-border treatment.")
def get_merchant_profile(tenant_id: UUID | None = Query(None, description="Super Admin only"), db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin)):
    return svc.get_profile(db, _profile_tenant(actor, tenant_id))


@router.put("/merchant-profile", response_model=MerchantTaxProfileResponse, summary="Save a business's tax profile")
def save_merchant_profile(payload: MerchantTaxProfileBody = Body(...), tenant_id: UUID | None = Query(None, description="Super Admin only"),
                          db: Session = Depends(get_db), actor: PayActor = Depends(require_merchant_or_admin)):
    return svc.save_profile(db, actor, _profile_tenant(actor, tenant_id), payload)
