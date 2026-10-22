"""Payments, multi-currency, tax, invoices, refunds and merchant settlement.

Money is stored as Numeric (never float). Every payment keeps the exchange rate and the tax rules it was priced with, so
the amounts can be explained and audited later whatever the rates or rules have become since. Raw card data is never
stored: a saved payment method is only the gateway's token plus brand / last four digits / expiry.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.database import Base

MONEY = Numeric(20, 4)
RATE = Numeric(24, 10)
PERCENT = Numeric(9, 6)


def _id():
    return Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


# ---------------------------------------------------------------- currency

class Currency(Base):
    __tablename__ = "pay_currencies"

    code = Column(String(3), primary_key=True)
    name = Column(String(100), nullable=False)
    symbol = Column(String(10))
    decimals = Column(Integer, nullable=False, default=2)         # JPY 0, USD 2, KWD 3
    is_active = Column(Boolean, nullable=False, default=True)
    min_amount = Column(MONEY, nullable=True)                      # smallest / largest single payment, in this currency
    max_amount = Column(MONEY, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ExchangeRate(Base):
    """1 `base_currency` = `rate` `quote_currency`. Rows are only added, so the history is the audit trail."""
    __tablename__ = "pay_exchange_rates"

    id = _id()
    base_currency = Column(String(3), nullable=False)
    quote_currency = Column(String(3), nullable=False)
    rate = Column(RATE, nullable=False)
    source = Column(String(10), nullable=False, default="auto")   # auto | manual | seed
    provider = Column(String(60))
    pinned = Column(Boolean, nullable=False, default=False)       # a pinned manual rate is not replaced by the automatic refresh
    created_by = Column(String(100))
    note = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_pay_rates_pair_time", "base_currency", "quote_currency", "created_at"),)


class CurrencyPreference(Base):
    __tablename__ = "pay_currency_preferences"

    user_id = Column(UUID(as_uuid=True), primary_key=True)
    currency = Column(String(3), nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


# ---------------------------------------------------------------- tax

class MerchantTaxProfile(Base):
    """Where a merchant supplies from, and their own tax registration. Decides intra-state / cross-border treatment."""
    __tablename__ = "pay_merchant_tax_profiles"

    tenant_id = Column(UUID(as_uuid=True), primary_key=True)
    country = Column(String(2), nullable=False)
    state = Column(String(60))
    tax_id = Column(String(40))
    tax_id_type = Column(String(20))                               # gstin | vat | other
    legal_name = Column(String(255))
    address = Column(Text)
    is_tax_registered = Column(Boolean, nullable=False, default=True)
    updated_by = Column(String(100))
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class TaxRule(Base):
    """One tax component for a place. A change never edits a row: it adds the next version and closes the old one."""
    __tablename__ = "pay_tax_rules"

    id = _id()
    rule_key = Column(UUID(as_uuid=True), nullable=False, index=True)    # shared by every version of the same rule
    version = Column(Integer, nullable=False, default=1)
    tax_type = Column(String(20), nullable=False)                        # gst | vat | sales_tax | service_tax
    name = Column(String(60), nullable=False)                            # CGST, SGST, IGST, VAT, ...
    country = Column(String(2), nullable=False, index=True)
    state = Column(String(60), nullable=True)                            # null = the whole country
    rate = Column(PERCENT, nullable=False)                               # percent, e.g. 18
    applies_to = Column(String(30), nullable=False, default="all")      # all | service | product | event | training | ...
    condition = Column(String(12), nullable=False, default="any")       # any | intra_state | inter_state
    rounding_mode = Column(String(12), nullable=False, default="half_up")   # half_up | half_even | up | down
    reverse_charge_allowed = Column(Boolean, nullable=False, default=False)
    priority = Column(Integer, nullable=False, default=100)
    valid_from = Column(DateTime, nullable=False, default=datetime.utcnow)
    valid_to = Column(DateTime, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    supersedes_id = Column(UUID(as_uuid=True), nullable=True)
    notes = Column(String(255))
    created_by = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class TaxExemption(Base):
    """A tax registration (GSTIN / VAT number: lets a business customer be charged reverse charge) or an exemption certificate."""
    __tablename__ = "pay_tax_exemptions"

    id = _id()
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    id_type = Column(String(24), nullable=False)                 # gstin | vat | exemption_certificate
    number = Column(String(60), nullable=False)
    country = Column(String(2), nullable=False)
    state = Column(String(60))
    legal_name = Column(String(255))
    status = Column(String(12), nullable=False, default="pending")   # pending | validated | rejected | revoked
    format_valid = Column(Boolean, nullable=False, default=False)
    valid_until = Column(Date, nullable=True)
    reviewed_by = Column(String(100))
    reviewed_at = Column(DateTime)
    review_note = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "id_type", "number", name="uq_pay_tax_exemption"),)


class TaxTransaction(Base):
    """The tax charged (positive) or given back (negative, on a refund) for a payment, one row per tax component."""
    __tablename__ = "pay_tax_transactions"

    id = _id()
    payment_id = Column(UUID(as_uuid=True), ForeignKey("pay_payments.id"), nullable=False, index=True)
    refund_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    invoice_id = Column(UUID(as_uuid=True), nullable=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    tax_rule_id = Column(UUID(as_uuid=True), nullable=True)
    rule_version = Column(Integer)
    tax_type = Column(String(20), nullable=False)
    name = Column(String(60), nullable=False)
    country = Column(String(2), nullable=False)
    state = Column(String(60))
    rate = Column(PERCENT, nullable=False)
    taxable_amount = Column(MONEY, nullable=False)               # item currency
    tax_amount = Column(MONEY, nullable=False)                   # item currency
    item_currency = Column(String(3), nullable=False)
    tax_amount_charge = Column(MONEY, nullable=False)            # charge currency
    charge_currency = Column(String(3), nullable=False)
    is_exempt = Column(Boolean, nullable=False, default=False)
    is_reverse_charge = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


# ---------------------------------------------------------------- payments

class Payment(Base):
    __tablename__ = "pay_payments"

    id = _id()
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)          # the merchant who is paid
    customer_user_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    customer_email = Column(String(255), index=True)
    purchase_type = Column(String(30), nullable=False)                         # event_order | cart_order | custom
    purchase_id = Column(String(64), nullable=False, index=True)
    attempt_no = Column(Integer, nullable=False, default=1)
    retry_of_id = Column(UUID(as_uuid=True), nullable=True)
    idempotency_key = Column(String(100), nullable=True, unique=True)
    # created | requires_action | processing | authorized | succeeded | failed | cancelled | expired | review
    # | partially_refunded | refunded
    status = Column(String(20), nullable=False, default="created", index=True)
    capture_method = Column(String(10), nullable=False, default="automatic")   # automatic | manual
    provider = Column(String(20), nullable=False)
    gateway_order_id = Column(String(120), index=True)
    gateway_payment_id = Column(String(120), index=True)
    saved_method_id = Column(UUID(as_uuid=True), nullable=True)
    method_brand = Column(String(30))
    method_last4 = Column(String(4))

    item_currency = Column(String(3), nullable=False)
    subtotal_amount = Column(MONEY, nullable=False)
    tax_amount = Column(MONEY, nullable=False, default=0)
    total_amount = Column(MONEY, nullable=False)
    charge_currency = Column(String(3), nullable=False)
    charge_subtotal = Column(MONEY, nullable=False)
    charge_tax = Column(MONEY, nullable=False, default=0)
    charge_total = Column(MONEY, nullable=False)                               # what the customer pays
    exchange_rate = Column(RATE, nullable=False, default=1)                    # 1 item currency = this many charge currency
    base_currency = Column(String(3), nullable=False)
    base_total = Column(MONEY, nullable=False)
    base_rate = Column(RATE, nullable=False, default=1)                        # 1 charge currency = this many base currency
    rate_snapshot = Column(JSONB)                                              # the rate rows used

    refunded_amount = Column(MONEY, nullable=False, default=0)                 # charge currency
    gateway_fee = Column(MONEY, nullable=True)                                 # charge currency
    settlement_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    billing = Column(JSONB)
    lines = Column(JSONB)
    tax_details = Column(JSONB)
    risk_score = Column(Integer)
    risk_decision = Column(String(10))                                         # allow | review | deny
    risk_reasons = Column(JSONB)
    next_action = Column(JSONB)
    failure_code = Column(String(60))
    failure_message = Column(String(255))
    needs_reconciliation = Column(Boolean, nullable=False, default=False, index=True)
    last_gateway_check_at = Column(DateTime)
    retry_reminder_sent_at = Column(DateTime)
    invoice_id = Column(UUID(as_uuid=True), nullable=True)
    extra = Column(JSONB)

    expires_at = Column(DateTime)
    authorized_at = Column(DateTime)
    captured_at = Column(DateTime, index=True)
    failed_at = Column(DateTime)
    cancelled_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_pay_payments_purchase", "purchase_type", "purchase_id"),
        Index("ix_pay_payments_tenant_created", "tenant_id", "created_at"),
    )


class PaymentTransaction(Base):
    """Every exchange with the gateway, and every state change: the trail for disputes and reconciliation."""
    __tablename__ = "pay_payment_transactions"

    id = _id()
    payment_id = Column(UUID(as_uuid=True), ForeignKey("pay_payments.id"), nullable=False, index=True)
    kind = Column(String(20), nullable=False)       # create | verify | authorize | capture | webhook | cancel | refund | reconcile | timeout
    status = Column(String(20))
    gateway_ref = Column(String(120))
    amount = Column(MONEY)
    currency = Column(String(3))
    detail = Column(JSONB)
    error = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class WebhookEvent(Base):
    __tablename__ = "pay_webhook_events"

    id = _id()
    provider = Column(String(20), nullable=False)
    event_id = Column(String(120), nullable=False)
    event_type = Column(String(60))
    payload = Column(JSONB)
    processed_at = Column(DateTime)
    error = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("provider", "event_id", name="uq_pay_webhook_event"),)


class SavedPaymentMethod(Base):
    __tablename__ = "pay_saved_methods"

    id = _id()
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    provider = Column(String(20), nullable=False)
    gateway_customer_id = Column(String(120))
    token = Column(String(160), nullable=False)       # the gateway's token / payment-method id; never a card number
    brand = Column(String(30))
    last4 = Column(String(4))
    exp_month = Column(Integer)
    exp_year = Column(Integer)
    holder_name = Column(String(120))
    is_default = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ---------------------------------------------------------------- invoices

class InvoiceSequence(Base):
    __tablename__ = "pay_invoice_sequences"

    series_key = Column(String(80), primary_key=True)
    last_number = Column(Integer, nullable=False, default=0)


class InvoiceTemplate(Base):
    """Branding / wording of a merchant's invoices. tenant_id null is the platform default."""
    __tablename__ = "pay_invoice_templates"

    id = _id()
    tenant_id = Column(UUID(as_uuid=True), nullable=True, unique=True)
    display_name = Column(String(255))
    logo_url = Column(Text)
    address = Column(Text)
    footer_text = Column(Text)
    terms = Column(Text)
    number_prefix = Column(String(10), nullable=False, default="INV")
    locale = Column(String(10), nullable=False, default="en-IN")
    accent_color = Column(String(9), default="#1f3a5f")
    show_exchange_rate = Column(Boolean, nullable=False, default=True)
    updated_by = Column(String(100))
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class Invoice(Base):
    __tablename__ = "pay_invoices"

    id = _id()
    doc_type = Column(String(12), nullable=False, default="invoice")        # invoice | credit_note | debit_note
    number = Column(String(40), nullable=False)
    series_key = Column(String(80), nullable=False)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    customer_user_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    payment_id = Column(UUID(as_uuid=True), ForeignKey("pay_payments.id"), nullable=False, index=True)
    refund_id = Column(UUID(as_uuid=True), nullable=True)
    parent_invoice_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    status = Column(String(10), nullable=False, default="issued")           # issued | void
    currency = Column(String(3), nullable=False)                             # what the customer paid in
    item_currency = Column(String(3), nullable=False)
    base_currency = Column(String(3), nullable=False)
    exchange_rate = Column(RATE, nullable=False, default=1)
    base_rate = Column(RATE, nullable=False, default=1)
    subtotal = Column(MONEY, nullable=False)
    tax_total = Column(MONEY, nullable=False, default=0)
    total = Column(MONEY, nullable=False)
    base_total = Column(MONEY, nullable=False)
    lines = Column(JSONB)
    tax_breakdown = Column(JSONB)
    billing = Column(JSONB)
    supplier = Column(JSONB)
    notes = Column(Text)
    reason = Column(String(255))
    template_id = Column(UUID(as_uuid=True), nullable=True)
    issued_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    emailed_at = Column(DateTime)
    email_count = Column(Integer, nullable=False, default=0)
    created_by = Column(String(100))

    __table_args__ = (UniqueConstraint("series_key", "number", name="uq_pay_invoice_number"),)


# ---------------------------------------------------------------- refunds

class Refund(Base):
    __tablename__ = "pay_refunds"

    id = _id()
    payment_id = Column(UUID(as_uuid=True), ForeignKey("pay_payments.id"), nullable=False, index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    requested_by = Column(UUID(as_uuid=True), nullable=True)
    requested_by_role = Column(String(20))
    amount = Column(MONEY, nullable=False)                    # charge currency
    currency = Column(String(3), nullable=False)
    refund_type = Column(String(10), nullable=False)          # full | partial
    reason = Column(String(500))
    # requested | approved | processing | completed | failed | rejected
    status = Column(String(12), nullable=False, default="requested", index=True)
    destination = Column(String(14), nullable=False, default="original")     # original | bank_transfer | wallet
    bank_reference = Column(String(120))
    gateway_refund_id = Column(String(120))
    credit_note_id = Column(UUID(as_uuid=True), nullable=True)
    tax_refunded = Column(MONEY, nullable=False, default=0)                  # charge currency
    decided_by = Column(String(100))
    decided_at = Column(DateTime)
    decision_reason = Column(String(500))
    approved_at = Column(DateTime)
    sla_due_at = Column(DateTime, index=True)
    escalated_at = Column(DateTime)
    completed_at = Column(DateTime)
    failure_reason = Column(String(255))
    settlement_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


# ---------------------------------------------------------------- settlement

class SettlementConfig(Base):
    __tablename__ = "pay_settlement_configs"

    tenant_id = Column(UUID(as_uuid=True), primary_key=True)
    schedule = Column(String(8), nullable=False, default="weekly")           # daily | weekly | monthly
    weekday = Column(Integer, nullable=False, default=0)                      # 0 = Monday (weekly)
    month_day = Column(Integer, nullable=False, default=1)                    # 1..28 (monthly)
    currency = Column(String(3), nullable=False)                               # payout currency
    commission_percent = Column(PERCENT, nullable=True)                        # null = the platform default
    fixed_fee = Column(MONEY, nullable=False, default=0)                       # per payment, payout currency
    min_payout = Column(MONEY, nullable=False, default=0)
    hold = Column(Boolean, nullable=False, default=False)
    payout_account = Column(JSONB)                                             # masked details only
    last_run_at = Column(DateTime)
    updated_by = Column(String(100))
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class MerchantSettlement(Base):
    __tablename__ = "pay_settlements"

    id = _id()
    reference = Column(String(30), nullable=False, unique=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    currency = Column(String(3), nullable=False)
    base_currency = Column(String(3), nullable=False)
    exchange_rate = Column(RATE, nullable=False, default=1)                  # 1 base currency = this many payout currency
    gross_amount = Column(MONEY, nullable=False, default=0)
    refund_amount = Column(MONEY, nullable=False, default=0)
    commission_amount = Column(MONEY, nullable=False, default=0)
    fee_amount = Column(MONEY, nullable=False, default=0)                     # gateway fees
    fixed_fee_amount = Column(MONEY, nullable=False, default=0)
    tax_collected = Column(MONEY, nullable=False, default=0)                  # included in gross, for information
    net_amount = Column(MONEY, nullable=False, default=0)
    items_count = Column(Integer, nullable=False, default=0)
    status = Column(String(12), nullable=False, default="pending", index=True)   # pending | paid | failed | on_hold | cancelled
    payout_reference = Column(String(120))
    paid_at = Column(DateTime)
    failure_reason = Column(String(255))
    created_by = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class SettlementItem(Base):
    __tablename__ = "pay_settlement_items"

    id = _id()
    settlement_id = Column(UUID(as_uuid=True), ForeignKey("pay_settlements.id"), nullable=False, index=True)
    kind = Column(String(8), nullable=False)                                  # payment | refund
    payment_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    refund_id = Column(UUID(as_uuid=True), nullable=True)
    charge_currency = Column(String(3), nullable=False)
    charge_amount = Column(MONEY, nullable=False)
    gross = Column(MONEY, nullable=False)                                      # payout currency; negative for a refund
    commission = Column(MONEY, nullable=False, default=0)
    fee = Column(MONEY, nullable=False, default=0)
    fixed_fee = Column(MONEY, nullable=False, default=0)
    net = Column(MONEY, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ReconciliationRun(Base):
    __tablename__ = "pay_reconciliation_runs"

    id = _id()
    kind = Column(String(12), nullable=False, default="gateway")
    provider = Column(String(20))
    period_start = Column(DateTime)
    period_end = Column(DateTime)
    total_rows = Column(Integer, nullable=False, default=0)
    matched = Column(Integer, nullable=False, default=0)
    mismatched = Column(Integer, nullable=False, default=0)
    created_by = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class ReconciliationItem(Base):
    __tablename__ = "pay_reconciliation_items"

    id = _id()
    run_id = Column(UUID(as_uuid=True), ForeignKey("pay_reconciliation_runs.id"), nullable=False, index=True)
    issue = Column(String(24), nullable=False)       # missing_internal | missing_gateway | amount_mismatch | status_mismatch | currency_mismatch | fee_mismatch | duplicate
    gateway_ref = Column(String(120))
    payment_id = Column(UUID(as_uuid=True), nullable=True)
    gateway_amount = Column(MONEY)
    internal_amount = Column(MONEY)
    currency = Column(String(3))
    gateway_status = Column(String(30))
    internal_status = Column(String(30))
    detail = Column(JSONB)
    resolved = Column(Boolean, nullable=False, default=False)
    resolved_by = Column(String(100))
    resolved_at = Column(DateTime)
    note = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ---------------------------------------------------------------- audit

class FinanceAuditLog(Base):
    """Who changed a tax rule, an exchange rate, a settlement setting or a refund decision, and when. Never edited."""
    __tablename__ = "pay_audit_log"

    id = _id()
    actor_id = Column(String(100))
    actor_name = Column(String(255))
    actor_role = Column(String(20))
    action = Column(String(40), nullable=False, index=True)
    entity_type = Column(String(30), nullable=False, index=True)
    entity_id = Column(String(64), index=True)
    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    before = Column(JSONB)
    after = Column(JSONB)
    reason = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
