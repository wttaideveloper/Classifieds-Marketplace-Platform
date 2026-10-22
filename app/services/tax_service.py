"""Tax: rules, exemptions, the calculation, and the tax history.

The rules live in the database, so a rate changes with an API call and no release. A change never edits a rule: it adds the
next version (valid from a date) and closes the old one, so a payment made last month can always be explained by the rule
that applied to it.

How a calculation goes, for each line:
  1. The place of supply is the customer's country and state. A customer with an approved exemption certificate pays no tax.
  2. The rules that apply are those in force on the date, for that country, that state (or the whole country), for that kind
     of item, and whose condition fits: `intra_state` needs the merchant and the customer in the same state, `inter_state`
     is every other case. This is how India's CGST + SGST (same state) and IGST (different state) are both described.
  3. A business customer abroad with an approved tax registration is not charged on rules that allow reverse charge.
  4. Each rule rounds its own tax with its own rounding mode, to the currency's decimals. Prices that include tax are split
     back into net and tax, with the last component taking the rounding remainder so the parts add up exactly.
"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.payment_model import MerchantTaxProfile, Payment, TaxExemption, TaxRule, TaxTransaction
from app.services import currency_service, finance_audit, payment_notifications
from app.utils import tax_ids
from app.utils.money import quantize, to_decimal

logger = logging.getLogger(__name__)

RULE_FIELDS = ("tax_type", "name", "country", "state", "rate", "applies_to", "condition", "rounding_mode",
               "reverse_charge_allowed", "priority", "valid_from", "valid_to", "is_active")


# ---------------------------------------------------------------- the calculation

@dataclass
class TaxItemIn:
    unit_amount: Decimal
    quantity: int = 1
    category: str = "service"
    description: str = ""
    ref: str = ""


@dataclass
class TaxContext:
    currency: str
    customer_country: str
    customer_state: str | None = None
    customer_is_business: bool = False
    customer_registration: TaxExemption | None = None       # an approved GSTIN / VAT number
    customer_exemption: TaxExemption | None = None          # an approved exemption certificate
    supplier_country: str | None = None
    supplier_state: str | None = None
    supplier_registered: bool = True
    prices_include_tax: bool = False
    at: datetime = field(default_factory=datetime.utcnow)
    warnings: list[str] = field(default_factory=list)


def _same(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and a.strip().lower() == b.strip().lower()


def _fits_condition(rule: TaxRule, ctx: TaxContext) -> bool:
    if rule.condition == "any":
        return True
    intra = _same(ctx.supplier_country, ctx.customer_country) and _same(ctx.supplier_state, ctx.customer_state)
    return intra if rule.condition == "intra_state" else not intra


def rules_in_force(db: Session, country: str, at: datetime) -> list[TaxRule]:
    return (
        db.query(TaxRule)
        .filter(TaxRule.country == country.upper(), TaxRule.is_active.is_(True), TaxRule.valid_from <= at,
                (TaxRule.valid_to.is_(None)) | (TaxRule.valid_to > at))
        .order_by(TaxRule.priority, TaxRule.name, TaxRule.id)
        .all()
    )


def _applicable(rules: list[TaxRule], category: str, ctx: TaxContext) -> list[TaxRule]:
    return [
        r for r in rules
        if (r.state is None or _same(r.state, ctx.customer_state)) and r.applies_to in ("all", category.lower()) and _fits_condition(r, ctx)
    ]


def calculate(db: Session, items: list[TaxItemIn], ctx: TaxContext) -> dict:
    decimals = currency_service.decimals_for(db, ctx.currency)
    exempt = ctx.customer_exemption is not None
    seller_untaxed = not ctx.supplier_registered
    cross_border = bool(ctx.supplier_country) and not _same(ctx.supplier_country, ctx.customer_country)
    reverse = bool(ctx.customer_is_business and ctx.customer_registration is not None and cross_border)
    rules = rules_in_force(db, ctx.customer_country, ctx.at)
    warnings = list(ctx.warnings)
    if not rules and not exempt and not seller_untaxed:
        warnings.append(f"no_tax_rule_for_{ctx.customer_country.upper()}")

    lines, breakdown_map, any_reverse = [], {}, False
    for index, item in enumerate(items):
        ref = item.ref or f"line-{index + 1}"
        gross_or_net = quantize(to_decimal(item.unit_amount) * item.quantity, decimals)
        chosen = [] if (exempt or seller_untaxed) else _applicable(rules, item.category, ctx)
        charged = [r for r in chosen if not (reverse and r.reverse_charge_allowed)]
        reverse_charged = [r for r in chosen if reverse and r.reverse_charge_allowed]
        any_reverse = any_reverse or bool(reverse_charged)
        components: list[tuple[TaxRule, Decimal, bool]] = []

        if ctx.prices_include_tax and charged:
            rate_sum = sum((to_decimal(r.rate) for r in charged), Decimal(0))
            net = quantize(gross_or_net / (1 + rate_sum / 100), decimals)
            total_tax = gross_or_net - net
            left = total_tax
            for position, rule in enumerate(charged):
                share = left if position == len(charged) - 1 else quantize(total_tax * to_decimal(rule.rate) / rate_sum, decimals, rule.rounding_mode)
                left -= share
                components.append((rule, share, False))
        else:
            net = gross_or_net
            for rule in charged:
                components.append((rule, quantize(net * to_decimal(rule.rate) / 100, decimals, rule.rounding_mode), False))
        for rule in reverse_charged:
            components.append((rule, Decimal(0).quantize(Decimal(1).scaleb(-decimals)), True))

        tax = sum((c[1] for c in components), Decimal(0)).quantize(Decimal(1).scaleb(-decimals))
        taxes = []
        for rule, amount, is_reverse in components:
            taxes.append({"rule_id": rule.id, "rule_version": rule.version, "tax_type": rule.tax_type, "name": rule.name, "rate": to_decimal(rule.rate),
                          "taxable_amount": net, "tax_amount": amount, "reverse_charge": is_reverse})
            key = (rule.tax_type, rule.name, to_decimal(rule.rate), rule.country, rule.state)
            row = breakdown_map.setdefault(key, {"tax_type": rule.tax_type, "name": rule.name, "rate": to_decimal(rule.rate), "country": rule.country,
                                                  "state": rule.state, "taxable_amount": Decimal(0), "tax_amount": Decimal(0)})
            row["taxable_amount"] += net
            row["tax_amount"] += amount
        lines.append({"ref": ref, "description": item.description, "category": item.category, "quantity": item.quantity,
                      "unit_amount": quantize(to_decimal(item.unit_amount), decimals), "net_amount": net, "tax_amount": tax,
                      "gross_amount": net + tax, "taxes": taxes})

    subtotal = sum((line["net_amount"] for line in lines), Decimal(0))
    tax_total = sum((line["tax_amount"] for line in lines), Decimal(0))
    note = None
    if any_reverse:
        note = "Reverse charge: the customer accounts for the tax"
    if seller_untaxed:
        note = "The seller is not registered for tax, so no tax is charged"
    return {
        "currency": ctx.currency, "prices_include_tax": ctx.prices_include_tax, "subtotal": subtotal, "tax_total": tax_total,
        "total": subtotal + tax_total, "is_exempt": exempt,
        "exempt_reason": f"Exemption certificate {ctx.customer_exemption.number}" if exempt else None,
        "reverse_charge": any_reverse, "note": note, "warnings": warnings,
        "jurisdiction": {"country": ctx.customer_country.upper(), "state": ctx.customer_state, "supplier_country": ctx.supplier_country,
                         "supplier_state": ctx.supplier_state},
        "lines": lines, "breakdown": list(breakdown_map.values()),
    }


def build_context(db: Session, *, currency: str, customer_country: str, customer_state: str | None, customer_tax_id: str | None,
                  customer_is_business: bool, tenant_id: UUID | None, user_id: UUID | None, prices_include_tax: bool = False,
                  at: datetime | None = None) -> TaxContext:
    ctx = TaxContext(currency=currency.upper(), customer_country=customer_country.upper(), customer_state=customer_state,
                     customer_is_business=customer_is_business, prices_include_tax=prices_include_tax, at=at or datetime.utcnow())
    profile = db.get(MerchantTaxProfile, tenant_id) if tenant_id else None
    if profile is not None:
        ctx.supplier_country, ctx.supplier_state, ctx.supplier_registered = profile.country, profile.state, profile.is_tax_registered
    else:
        ctx.warnings.append("supplier_tax_profile_missing")
    if user_id is not None:
        today = ctx.at.date()
        ctx.customer_exemption = find_approved(db, user_id, ctx.customer_country, ("exemption_certificate",), today, state=customer_state)
        if customer_is_business:
            ctx.customer_registration = find_approved(db, user_id, ctx.customer_country, ("gstin", "vat"), today, number=customer_tax_id)
            if customer_tax_id and ctx.customer_registration is None:
                ctx.warnings.append("customer_tax_id_not_approved")
    elif customer_tax_id:
        ctx.warnings.append("customer_tax_id_not_approved")
    return ctx


def find_approved(db: Session, user_id: UUID, country: str, id_types: tuple[str, ...], today: date, *, number: str | None = None,
                  state: str | None = None) -> TaxExemption | None:
    query = db.query(TaxExemption).filter(
        TaxExemption.user_id == user_id, TaxExemption.country == country.upper(), TaxExemption.status == "validated",
        TaxExemption.id_type.in_(id_types),
    )
    if number:
        query = query.filter(TaxExemption.number == tax_ids.normalize(number))
    for row in query.order_by(TaxExemption.created_at.desc()).all():
        if row.valid_until is not None and row.valid_until < today:
            continue
        if row.id_type == "exemption_certificate" and row.state and not _same(row.state, state):
            continue
        return row
    return None


def record_transactions(db: Session, payment: Payment, calc: dict, *, charge_per_item: Decimal, sign: int = 1,
                        refund_id: UUID | None = None, share: Decimal = Decimal(1)) -> list[TaxTransaction]:
    """One TaxTransaction per tax component, in the item and the charge currency. A refund records the same rows negated
    (and scaled by `share`, the part of the payment refunded). Called once a payment has succeeded, so the table holds
    realised tax only."""
    charge_decimals = currency_service.decimals_for(db, payment.charge_currency)
    item_decimals = currency_service.decimals_for(db, payment.item_currency)
    rows = []
    jurisdiction = calc.get("jurisdiction", {})
    for line in calc.get("lines", []):
        for tax in line["taxes"]:
            amount = quantize(to_decimal(tax["tax_amount"]) * share, item_decimals) * sign
            taxable = quantize(to_decimal(tax["taxable_amount"]) * share, item_decimals) * sign
            row = TaxTransaction(
                payment_id=payment.id, refund_id=refund_id, tenant_id=payment.tenant_id, tax_rule_id=UUID(str(tax["rule_id"])) if tax.get("rule_id") else None,
                rule_version=tax.get("rule_version"), tax_type=tax["tax_type"], name=tax["name"],
                country=jurisdiction.get("country") or "", state=jurisdiction.get("state"), rate=to_decimal(tax["rate"]),
                taxable_amount=taxable, tax_amount=amount, item_currency=payment.item_currency,
                tax_amount_charge=quantize(amount * charge_per_item, charge_decimals), charge_currency=payment.charge_currency,
                is_exempt=bool(calc.get("is_exempt")), is_reverse_charge=bool(tax.get("reverse_charge")),
            )
            db.add(row)
            rows.append(row)
    return rows


# ---------------------------------------------------------------- rules

def _validate_rule_place(country: str, state: str | None) -> None:
    try:
        import pycountry

        if pycountry.countries.get(alpha_2=country.upper()) is None:
            raise HTTPException(status_code=422, detail=f"{country} is not a country code")
    except ImportError:
        pass


def rule_is_current(rule: TaxRule, now: datetime | None = None) -> bool:
    now = now or datetime.utcnow()
    return bool(rule.is_active) and rule.valid_from <= now and (rule.valid_to is None or rule.valid_to > now)


def list_rules(db: Session, *, country: str | None = None, state: str | None = None, tax_type: str | None = None,
               current_only: bool = True, include_inactive: bool = False) -> list[TaxRule]:
    query = db.query(TaxRule)
    if country:
        query = query.filter(TaxRule.country == country.upper())
    if state:
        query = query.filter(func.lower(TaxRule.state) == state.strip().lower())
    if tax_type:
        query = query.filter(TaxRule.tax_type == tax_type)
    if not include_inactive:
        query = query.filter(TaxRule.is_active.is_(True))
    rows = query.order_by(TaxRule.country, TaxRule.state, TaxRule.priority, TaxRule.name, TaxRule.version.desc()).all()
    if current_only:
        now = datetime.utcnow()
        rows = [r for r in rows if rule_is_current(r, now)]
    return rows


def get_rule(db: Session, rule_id: UUID) -> TaxRule:
    row = db.get(TaxRule, rule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Tax rule not found")
    return row


def create_rule(db: Session, actor, payload, access_token: str | None = None) -> TaxRule:
    _validate_rule_place(payload.country, payload.state)
    if payload.valid_to and payload.valid_from and payload.valid_to <= payload.valid_from:
        raise HTTPException(status_code=422, detail="valid_to must be after valid_from")
    rule = TaxRule(
        rule_key=uuid4(), version=1, tax_type=payload.tax_type, name=payload.name.strip(), country=payload.country, state=(payload.state or "").strip() or None,
        rate=payload.rate, applies_to=payload.applies_to, condition=payload.condition, rounding_mode=payload.rounding_mode,
        reverse_charge_allowed=payload.reverse_charge_allowed, priority=payload.priority, valid_from=payload.valid_from or datetime.utcnow(),
        valid_to=payload.valid_to, notes=payload.notes, created_by=actor.id,
    )
    db.add(rule)
    db.flush()
    finance_audit.record(db, actor, "tax_rule_created", "tax_rule", rule.id, after=finance_audit.snapshot(rule, *RULE_FIELDS, "rule_key", "version"))
    db.commit()
    db.refresh(rule)
    payment_notifications.notify_tax_rule_updated(rule.id, "created", actor.name, access_token=access_token)
    return rule


def change_rule(db: Session, actor, rule_id: UUID, payload, access_token: str | None = None) -> TaxRule:
    """Adds the next version. The version being replaced keeps applying until the new one starts."""
    old = get_rule(db, rule_id)
    latest = db.query(TaxRule).filter(TaxRule.rule_key == old.rule_key).order_by(TaxRule.version.desc()).first()
    if latest.id != old.id:
        raise HTTPException(status_code=409, detail=f"Version {latest.version} is the latest one of this rule. Change that one")
    if not old.is_active:
        raise HTTPException(status_code=409, detail="This rule is switched off. Create a new rule instead")
    data = payload.model_dump(exclude_unset=True)
    reason = data.pop("reason", None)
    clear_state = data.pop("clear_state", False)
    starts = data.pop("valid_from", None) or datetime.utcnow()
    if starts < old.valid_from:
        raise HTTPException(status_code=422, detail="The new version cannot start before the version it replaces")
    ends = data.pop("valid_to", None)
    if ends is not None and ends <= starts:
        raise HTTPException(status_code=422, detail="valid_to must be after valid_from")
    changes = {k: v for k, v in data.items() if v is not None}
    if not changes and not clear_state and ends is None:
        raise HTTPException(status_code=422, detail="Nothing to change")
    before = finance_audit.snapshot(old, *RULE_FIELDS, "version")
    new = TaxRule(
        rule_key=old.rule_key, version=old.version + 1, supersedes_id=old.id, created_by=actor.id, is_active=True,
        **{f: getattr(old, f) for f in ("tax_type", "name", "country", "state", "rate", "applies_to", "condition", "rounding_mode",
                                         "reverse_charge_allowed", "priority", "notes")},
    )
    for key, value in changes.items():
        setattr(new, key, value)
    if clear_state:
        new.state = None
    if "state" in changes:
        new.state = (changes["state"] or "").strip() or None
    new.valid_from, new.valid_to = starts, ends
    if old.valid_to is None or old.valid_to > starts:
        old.valid_to = starts
    db.add(new)
    db.flush()
    finance_audit.record(db, actor, "tax_rule_changed", "tax_rule", new.id, before=before,
                         after=finance_audit.snapshot(new, *RULE_FIELDS, "version"), reason=reason)
    db.commit()
    db.refresh(new)
    payment_notifications.notify_tax_rule_updated(new.id, "changed", actor.name, access_token=access_token)
    return new


def deactivate_rule(db: Session, actor, rule_id: UUID, reason: str | None = None, access_token: str | None = None) -> TaxRule:
    """Switches off every version of the rule from now on. Payments already made keep the rule they used."""
    rule = get_rule(db, rule_id)
    versions = db.query(TaxRule).filter(TaxRule.rule_key == rule.rule_key).all()
    if not any(v.is_active for v in versions):
        raise HTTPException(status_code=409, detail="This rule is already switched off")
    now = datetime.utcnow()
    before = finance_audit.snapshot(rule, *RULE_FIELDS, "version")
    for version in versions:
        if version.valid_to is None or version.valid_to > now:
            version.valid_to = max(now, version.valid_from)
        version.is_active = False
    finance_audit.record(db, actor, "tax_rule_deactivated", "tax_rule", rule.id, before=before, after={"is_active": False}, reason=reason)
    db.commit()
    db.refresh(rule)
    payment_notifications.notify_tax_rule_updated(rule.id, "deactivated", actor.name, access_token=access_token)
    return rule


def rule_versions(db: Session, rule_id: UUID) -> list[TaxRule]:
    rule = get_rule(db, rule_id)
    return db.query(TaxRule).filter(TaxRule.rule_key == rule.rule_key).order_by(TaxRule.version.desc()).all()


# ---------------------------------------------------------------- exemptions and registrations

def check_number(id_type: str, number: str, country: str | None) -> tax_ids.TaxIdCheck:
    return tax_ids.check_tax_id(id_type, number, country)


def register_exemption(db: Session, user_id: UUID, payload) -> TaxExemption:
    check = check_number(payload.id_type, payload.number, payload.country)
    if not check.valid:
        raise HTTPException(status_code=422, detail=check.message)
    if payload.id_type == "gstin" and payload.country != "IN":
        raise HTTPException(status_code=422, detail="A GSTIN is an Indian registration. Use country IN")
    number = check.normalized
    existing = db.query(TaxExemption).filter(TaxExemption.user_id == user_id, TaxExemption.id_type == payload.id_type, TaxExemption.number == number).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="You have already registered this number")
    row = TaxExemption(
        user_id=user_id, id_type=payload.id_type, number=number, country=payload.country, state=payload.state or check.state,
        legal_name=payload.legal_name, valid_until=payload.valid_until, format_valid=True, status="pending",
    )
    if settings.TAX_ID_AUTO_APPROVE_VALID_FORMAT and payload.id_type in ("gstin", "vat"):
        row.status, row.review_note, row.reviewed_at = "validated", "Approved automatically: the number has a valid format", datetime.utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def decide_exemption(db: Session, actor, exemption_id: UUID, action: str, note: str | None) -> TaxExemption:
    row = db.get(TaxExemption, exemption_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Registration not found")
    target = {"approve": "validated", "reject": "rejected", "revoke": "revoked"}[action]
    if action == "revoke" and row.status != "validated":
        raise HTTPException(status_code=409, detail="Only an approved registration can be revoked")
    if action in ("approve", "reject") and row.status != "pending":
        raise HTTPException(status_code=409, detail=f"This registration is already {row.status}")
    if action in ("reject", "revoke") and not (note or "").strip():
        raise HTTPException(status_code=422, detail="Give a note explaining why")
    before = {"status": row.status}
    row.status, row.reviewed_by, row.reviewed_at, row.review_note = target, actor.id, datetime.utcnow(), note
    finance_audit.record(db, actor, f"tax_registration_{target}", "tax_exemption", row.id, before=before, after={"status": target, "number": row.number}, reason=note)
    db.commit()
    db.refresh(row)
    return row


def list_exemptions(db: Session, *, user_id: UUID | None, status: str | None, page: int, page_size: int) -> tuple[list[TaxExemption], int]:
    query = db.query(TaxExemption)
    if user_id is not None:
        query = query.filter(TaxExemption.user_id == user_id)
    if status:
        query = query.filter(TaxExemption.status == status)
    total = query.count()
    return query.order_by(TaxExemption.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all(), total


# ---------------------------------------------------------------- merchant profile

def get_profile(db: Session, tenant_id: UUID) -> MerchantTaxProfile:
    row = db.get(MerchantTaxProfile, tenant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No tax profile yet. Save one with PUT /tax/merchant-profile")
    return row


def save_profile(db: Session, actor, tenant_id: UUID, payload) -> MerchantTaxProfile:
    _validate_rule_place(payload.country, payload.state)
    if payload.tax_id:
        id_type = payload.tax_id_type or ("gstin" if payload.country == "IN" else "vat")
        if id_type in ("gstin", "vat"):
            check = check_number(id_type, payload.tax_id, payload.country)
            if not check.valid:
                raise HTTPException(status_code=422, detail=check.message)
            payload.tax_id = check.normalized
            payload.tax_id_type = id_type
    row = db.get(MerchantTaxProfile, tenant_id)
    before = finance_audit.snapshot(row, "country", "state", "tax_id", "is_tax_registered") if row else None
    if row is None:
        row = MerchantTaxProfile(tenant_id=tenant_id)
        db.add(row)
    for key in ("country", "state", "tax_id", "tax_id_type", "legal_name", "address", "is_tax_registered"):
        setattr(row, key, getattr(payload, key))
    row.updated_by = actor.id
    finance_audit.record(db, actor, "merchant_tax_profile_saved", "merchant_tax_profile", tenant_id, before=before,
                         after=finance_audit.snapshot(row, "country", "state", "tax_id", "is_tax_registered"), tenant_id=tenant_id)
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------- history and reports

def _scope(query, actor, tenant_id: UUID | None):
    if actor.role == "merchant":
        return query.filter(TaxTransaction.tenant_id == actor.tenant_id)
    if actor.role == "customer":
        return query.join(Payment, Payment.id == TaxTransaction.payment_id).filter(Payment.customer_user_id == actor.user_uuid)
    return query.filter(TaxTransaction.tenant_id == tenant_id) if tenant_id else query


def tax_history(db: Session, actor, *, tenant_id: UUID | None = None, payment_id: UUID | None = None, country: str | None = None,
                tax_type: str | None = None, date_from: datetime | None = None, date_to: datetime | None = None,
                page: int = 1, page_size: int = 50) -> tuple[list[TaxTransaction], int]:
    query = _scope(db.query(TaxTransaction), actor, tenant_id)
    if payment_id:
        query = query.filter(TaxTransaction.payment_id == payment_id)
    if country:
        query = query.filter(TaxTransaction.country == country.upper())
    if tax_type:
        query = query.filter(TaxTransaction.tax_type == tax_type)
    if date_from:
        query = query.filter(TaxTransaction.created_at >= date_from)
    if date_to:
        query = query.filter(TaxTransaction.created_at <= date_to)
    total = query.count()
    return query.order_by(TaxTransaction.created_at.desc(), TaxTransaction.id).offset((page - 1) * page_size).limit(page_size).all(), total


def tax_report(db: Session, actor, *, tenant_id: UUID | None = None, date_from: datetime | None = None, date_to: datetime | None = None,
               group_by: str = "tax_type") -> dict:
    """Tax collected (net of refunds) grouped by tax_type, country, state, name, month or day, with the amounts in the base currency."""
    query = _scope(db.query(TaxTransaction, Payment.base_rate).join(Payment, Payment.id == TaxTransaction.payment_id), actor, None) \
        if actor.role != "customer" else None
    if query is None:
        raise HTTPException(status_code=403, detail="Tax reports are for merchants and the platform")
    if tenant_id and actor.role == "super_admin":
        query = query.filter(TaxTransaction.tenant_id == tenant_id)
    if date_from:
        query = query.filter(TaxTransaction.created_at >= date_from)
    if date_to:
        query = query.filter(TaxTransaction.created_at <= date_to)
    base = currency_service.base_currency()
    base_decimals = currency_service.decimals_for(db, base)
    groups: dict = {}
    for tx, base_rate in query.all():
        key_value = {"tax_type": tx.tax_type, "country": tx.country, "state": tx.state or "", "name": tx.name,
                     "day": tx.created_at.strftime("%Y-%m-%d"), "month": tx.created_at.strftime("%Y-%m")}[group_by]
        bucket = groups.setdefault(key_value, {group_by: key_value, "transactions": 0, "taxable_amount": {}, "tax_amount": {}, "tax_amount_base": Decimal(0),
                                               "reverse_charge_taxable": {}, "exempt_taxable": {}})
        bucket["transactions"] += 1
        bucket["tax_amount"][tx.item_currency] = bucket["tax_amount"].get(tx.item_currency, Decimal(0)) + to_decimal(tx.tax_amount)
        bucket["taxable_amount"][tx.item_currency] = bucket["taxable_amount"].get(tx.item_currency, Decimal(0)) + to_decimal(tx.taxable_amount)
        bucket["tax_amount_base"] += to_decimal(tx.tax_amount_charge) * to_decimal(base_rate)
        if tx.is_reverse_charge:
            bucket["reverse_charge_taxable"][tx.item_currency] = bucket["reverse_charge_taxable"].get(tx.item_currency, Decimal(0)) + to_decimal(tx.taxable_amount)
        if tx.is_exempt:
            bucket["exempt_taxable"][tx.item_currency] = bucket["exempt_taxable"].get(tx.item_currency, Decimal(0)) + to_decimal(tx.taxable_amount)
    rows = sorted(groups.values(), key=lambda r: str(r[group_by]))
    for row in rows:
        row["tax_amount_base"] = quantize(row["tax_amount_base"], base_decimals)
        for name in ("taxable_amount", "tax_amount", "reverse_charge_taxable", "exempt_taxable"):
            row[name] = {code: quantize(value, currency_service.decimals_for(db, code)) for code, value in row[name].items()}
    return {"group_by": group_by, "base_currency": base, "rows": rows,
            "total_tax_base": quantize(sum((r["tax_amount_base"] for r in rows), Decimal(0)), base_decimals)}
