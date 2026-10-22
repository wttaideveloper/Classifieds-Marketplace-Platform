"""payments, multi-currency, tax, invoices, refunds and merchant settlement

Creates the pay_* tables (currencies, exchange rates, tax rules and exemptions, payments and their gateway trail, saved payment
methods, invoices and their numbering, refunds, settlement, reconciliation and the finance audit log) and adds the usual
currencies. Rates are not seeded: add them with POST /currencies/rates or POST /currencies/rates/refresh.

Revision ID: b7e3d9a1c5f2
Revises: a6d2c8e4b1f7
Create Date: 2026-10-22 00:00:00.000000

"""
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b7e3d9a1c5f2"
down_revision = "a6d2c8e4b1f7"
branch_labels = None
depends_on = None

CURRENCIES = [
    # code, name, symbol, decimals, active
    ("INR", "Indian Rupee", "Rs", 2, True), ("USD", "US Dollar", "$", 2, True), ("EUR", "Euro", "EUR", 2, True),
    ("GBP", "Pound Sterling", "GBP", 2, True), ("AED", "UAE Dirham", "AED", 2, True), ("SGD", "Singapore Dollar", "S$", 2, True),
    ("AUD", "Australian Dollar", "A$", 2, False), ("CAD", "Canadian Dollar", "C$", 2, False), ("JPY", "Japanese Yen", "JPY", 0, False),
    ("KWD", "Kuwaiti Dinar", "KD", 3, False),
]


def upgrade():
    op.create_table(
        "pay_audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("actor_id", sa.String(length=100), nullable=True),
        sa.Column("actor_name", sa.String(length=255), nullable=True),
        sa.Column("actor_role", sa.String(length=20), nullable=True),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("entity_type", sa.String(length=30), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_pay_audit_log_action", "pay_audit_log", ['action'], unique=False)
    op.create_index("ix_pay_audit_log_created_at", "pay_audit_log", ['created_at'], unique=False)
    op.create_index("ix_pay_audit_log_entity_id", "pay_audit_log", ['entity_id'], unique=False)
    op.create_index("ix_pay_audit_log_entity_type", "pay_audit_log", ['entity_type'], unique=False)
    op.create_index("ix_pay_audit_log_tenant_id", "pay_audit_log", ['tenant_id'], unique=False)
    op.create_table(
        "pay_currencies",
        sa.Column("code", sa.String(length=3), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("symbol", sa.String(length=10), nullable=True),
        sa.Column("decimals", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("min_amount", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("max_amount", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "pay_currency_preferences",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "pay_exchange_rates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("quote_currency", sa.String(length=3), nullable=False),
        sa.Column("rate", sa.Numeric(precision=24, scale=10), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("provider", sa.String(length=60), nullable=True),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_pay_rates_pair_time", "pay_exchange_rates", ['base_currency', 'quote_currency', 'created_at'], unique=False)
    op.create_table(
        "pay_invoice_sequences",
        sa.Column("series_key", sa.String(length=80), primary_key=True, nullable=False),
        sa.Column("last_number", sa.Integer(), nullable=False),
    )
    op.create_table(
        "pay_invoice_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("logo_url", sa.Text(), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("footer_text", sa.Text(), nullable=True),
        sa.Column("terms", sa.Text(), nullable=True),
        sa.Column("number_prefix", sa.String(length=10), nullable=False),
        sa.Column("locale", sa.String(length=10), nullable=False),
        sa.Column("accent_color", sa.String(length=9), nullable=True),
        sa.Column("show_exchange_rate", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.String(length=100), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(*['tenant_id']),
    )
    op.create_table(
        "pay_merchant_tax_profiles",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("state", sa.String(length=60), nullable=True),
        sa.Column("tax_id", sa.String(length=40), nullable=True),
        sa.Column("tax_id_type", sa.String(length=20), nullable=True),
        sa.Column("legal_name", sa.String(length=255), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("is_tax_registered", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.String(length=100), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "pay_payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("customer_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("customer_email", sa.String(length=255), nullable=True),
        sa.Column("purchase_type", sa.String(length=30), nullable=False),
        sa.Column("purchase_id", sa.String(length=64), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("retry_of_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("capture_method", sa.String(length=10), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("gateway_order_id", sa.String(length=120), nullable=True),
        sa.Column("gateway_payment_id", sa.String(length=120), nullable=True),
        sa.Column("saved_method_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("method_brand", sa.String(length=30), nullable=True),
        sa.Column("method_last4", sa.String(length=4), nullable=True),
        sa.Column("item_currency", sa.String(length=3), nullable=False),
        sa.Column("subtotal_amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("tax_amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("total_amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("charge_currency", sa.String(length=3), nullable=False),
        sa.Column("charge_subtotal", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("charge_tax", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("charge_total", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("exchange_rate", sa.Numeric(precision=24, scale=10), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("base_total", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("base_rate", sa.Numeric(precision=24, scale=10), nullable=False),
        sa.Column("rate_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("refunded_amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("gateway_fee", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("settlement_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("billing", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("lines", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tax_details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("risk_score", sa.Integer(), nullable=True),
        sa.Column("risk_decision", sa.String(length=10), nullable=True),
        sa.Column("risk_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("next_action", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("failure_code", sa.String(length=60), nullable=True),
        sa.Column("failure_message", sa.String(length=255), nullable=True),
        sa.Column("needs_reconciliation", sa.Boolean(), nullable=False),
        sa.Column("last_gateway_check_at", sa.DateTime(), nullable=True),
        sa.Column("retry_reminder_sent_at", sa.DateTime(), nullable=True),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("authorized_at", sa.DateTime(), nullable=True),
        sa.Column("captured_at", sa.DateTime(), nullable=True),
        sa.Column("failed_at", sa.DateTime(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(*['idempotency_key']),
    )
    op.create_index("ix_pay_payments_captured_at", "pay_payments", ['captured_at'], unique=False)
    op.create_index("ix_pay_payments_created_at", "pay_payments", ['created_at'], unique=False)
    op.create_index("ix_pay_payments_customer_email", "pay_payments", ['customer_email'], unique=False)
    op.create_index("ix_pay_payments_customer_user_id", "pay_payments", ['customer_user_id'], unique=False)
    op.create_index("ix_pay_payments_gateway_order_id", "pay_payments", ['gateway_order_id'], unique=False)
    op.create_index("ix_pay_payments_gateway_payment_id", "pay_payments", ['gateway_payment_id'], unique=False)
    op.create_index("ix_pay_payments_needs_reconciliation", "pay_payments", ['needs_reconciliation'], unique=False)
    op.create_index("ix_pay_payments_purchase", "pay_payments", ['purchase_type', 'purchase_id'], unique=False)
    op.create_index("ix_pay_payments_purchase_id", "pay_payments", ['purchase_id'], unique=False)
    op.create_index("ix_pay_payments_settlement_id", "pay_payments", ['settlement_id'], unique=False)
    op.create_index("ix_pay_payments_status", "pay_payments", ['status'], unique=False)
    op.create_index("ix_pay_payments_tenant_created", "pay_payments", ['tenant_id', 'created_at'], unique=False)
    op.create_index("ix_pay_payments_tenant_id", "pay_payments", ['tenant_id'], unique=False)
    op.create_table(
        "pay_reconciliation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=True),
        sa.Column("period_start", sa.DateTime(), nullable=True),
        sa.Column("period_end", sa.DateTime(), nullable=True),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("matched", sa.Integer(), nullable=False),
        sa.Column("mismatched", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_pay_reconciliation_runs_created_at", "pay_reconciliation_runs", ['created_at'], unique=False)
    op.create_table(
        "pay_saved_methods",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("gateway_customer_id", sa.String(length=120), nullable=True),
        sa.Column("token", sa.String(length=160), nullable=False),
        sa.Column("brand", sa.String(length=30), nullable=True),
        sa.Column("last4", sa.String(length=4), nullable=True),
        sa.Column("exp_month", sa.Integer(), nullable=True),
        sa.Column("exp_year", sa.Integer(), nullable=True),
        sa.Column("holder_name", sa.String(length=120), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_pay_saved_methods_user_id", "pay_saved_methods", ['user_id'], unique=False)
    op.create_table(
        "pay_settlement_configs",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("schedule", sa.String(length=8), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("month_day", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("commission_percent", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("fixed_fee", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("min_payout", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("hold", sa.Boolean(), nullable=False),
        sa.Column("payout_account", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("updated_by", sa.String(length=100), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "pay_settlements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("reference", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("exchange_rate", sa.Numeric(precision=24, scale=10), nullable=False),
        sa.Column("gross_amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("refund_amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("commission_amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("fee_amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("fixed_fee_amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("tax_collected", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("net_amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("items_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("payout_reference", sa.String(length=120), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("failure_reason", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(*['reference']),
    )
    op.create_index("ix_pay_settlements_created_at", "pay_settlements", ['created_at'], unique=False)
    op.create_index("ix_pay_settlements_status", "pay_settlements", ['status'], unique=False)
    op.create_index("ix_pay_settlements_tenant_id", "pay_settlements", ['tenant_id'], unique=False)
    op.create_table(
        "pay_tax_exemptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("id_type", sa.String(length=24), nullable=False),
        sa.Column("number", sa.String(length=60), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("state", sa.String(length=60), nullable=True),
        sa.Column("legal_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("format_valid", sa.Boolean(), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("reviewed_by", sa.String(length=100), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("review_note", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(*['user_id', 'id_type', 'number'], name="uq_pay_tax_exemption"),
    )
    op.create_index("ix_pay_tax_exemptions_user_id", "pay_tax_exemptions", ['user_id'], unique=False)
    op.create_table(
        "pay_tax_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("rule_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("tax_type", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("state", sa.String(length=60), nullable=True),
        sa.Column("rate", sa.Numeric(precision=9, scale=6), nullable=False),
        sa.Column("applies_to", sa.String(length=30), nullable=False),
        sa.Column("condition", sa.String(length=12), nullable=False),
        sa.Column("rounding_mode", sa.String(length=12), nullable=False),
        sa.Column("reverse_charge_allowed", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("valid_from", sa.DateTime(), nullable=False),
        sa.Column("valid_to", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_pay_tax_rules_country", "pay_tax_rules", ['country'], unique=False)
    op.create_index("ix_pay_tax_rules_is_active", "pay_tax_rules", ['is_active'], unique=False)
    op.create_index("ix_pay_tax_rules_rule_key", "pay_tax_rules", ['rule_key'], unique=False)
    op.create_table(
        "pay_webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("event_id", sa.String(length=120), nullable=False),
        sa.Column("event_type", sa.String(length=60), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(*['provider', 'event_id'], name="uq_pay_webhook_event"),
    )
    op.create_table(
        "pay_invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("doc_type", sa.String(length=12), nullable=False),
        sa.Column("number", sa.String(length=40), nullable=False),
        sa.Column("series_key", sa.String(length=80), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("customer_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("refund_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parent_invoice_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("item_currency", sa.String(length=3), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("exchange_rate", sa.Numeric(precision=24, scale=10), nullable=False),
        sa.Column("base_rate", sa.Numeric(precision=24, scale=10), nullable=False),
        sa.Column("subtotal", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("tax_total", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("total", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("base_total", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("lines", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tax_breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("billing", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("supplier", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.Column("emailed_at", sa.DateTime(), nullable=True),
        sa.Column("email_count", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(['payment_id'], ['pay_payments.id']),
        sa.UniqueConstraint(*['series_key', 'number'], name="uq_pay_invoice_number"),
    )
    op.create_index("ix_pay_invoices_customer_user_id", "pay_invoices", ['customer_user_id'], unique=False)
    op.create_index("ix_pay_invoices_issued_at", "pay_invoices", ['issued_at'], unique=False)
    op.create_index("ix_pay_invoices_parent_invoice_id", "pay_invoices", ['parent_invoice_id'], unique=False)
    op.create_index("ix_pay_invoices_payment_id", "pay_invoices", ['payment_id'], unique=False)
    op.create_index("ix_pay_invoices_tenant_id", "pay_invoices", ['tenant_id'], unique=False)
    op.create_table(
        "pay_payment_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("gateway_ref", sa.String(length=120), nullable=True),
        sa.Column("amount", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['payment_id'], ['pay_payments.id']),
    )
    op.create_index("ix_pay_payment_transactions_created_at", "pay_payment_transactions", ['created_at'], unique=False)
    op.create_index("ix_pay_payment_transactions_payment_id", "pay_payment_transactions", ['payment_id'], unique=False)
    op.create_table(
        "pay_reconciliation_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("issue", sa.String(length=24), nullable=False),
        sa.Column("gateway_ref", sa.String(length=120), nullable=True),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("gateway_amount", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("internal_amount", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("gateway_status", sa.String(length=30), nullable=True),
        sa.Column("internal_status", sa.String(length=30), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        sa.Column("resolved_by", sa.String(length=100), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['run_id'], ['pay_reconciliation_runs.id']),
    )
    op.create_index("ix_pay_reconciliation_items_run_id", "pay_reconciliation_items", ['run_id'], unique=False)
    op.create_table(
        "pay_refunds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requested_by_role", sa.String(length=20), nullable=True),
        sa.Column("amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("refund_type", sa.String(length=10), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("destination", sa.String(length=14), nullable=False),
        sa.Column("bank_reference", sa.String(length=120), nullable=True),
        sa.Column("gateway_refund_id", sa.String(length=120), nullable=True),
        sa.Column("credit_note_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tax_refunded", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("decided_by", sa.String(length=100), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decision_reason", sa.String(length=500), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("sla_due_at", sa.DateTime(), nullable=True),
        sa.Column("escalated_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("failure_reason", sa.String(length=255), nullable=True),
        sa.Column("settlement_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['payment_id'], ['pay_payments.id']),
    )
    op.create_index("ix_pay_refunds_created_at", "pay_refunds", ['created_at'], unique=False)
    op.create_index("ix_pay_refunds_payment_id", "pay_refunds", ['payment_id'], unique=False)
    op.create_index("ix_pay_refunds_settlement_id", "pay_refunds", ['settlement_id'], unique=False)
    op.create_index("ix_pay_refunds_sla_due_at", "pay_refunds", ['sla_due_at'], unique=False)
    op.create_index("ix_pay_refunds_status", "pay_refunds", ['status'], unique=False)
    op.create_index("ix_pay_refunds_tenant_id", "pay_refunds", ['tenant_id'], unique=False)
    op.create_table(
        "pay_settlement_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("settlement_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("refund_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("charge_currency", sa.String(length=3), nullable=False),
        sa.Column("charge_amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("gross", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("commission", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("fee", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("fixed_fee", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("net", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['settlement_id'], ['pay_settlements.id']),
    )
    op.create_index("ix_pay_settlement_items_payment_id", "pay_settlement_items", ['payment_id'], unique=False)
    op.create_index("ix_pay_settlement_items_settlement_id", "pay_settlement_items", ['settlement_id'], unique=False)
    op.create_table(
        "pay_tax_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("refund_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("invoice_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tax_rule_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rule_version", sa.Integer(), nullable=True),
        sa.Column("tax_type", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("state", sa.String(length=60), nullable=True),
        sa.Column("rate", sa.Numeric(precision=9, scale=6), nullable=False),
        sa.Column("taxable_amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("tax_amount", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("item_currency", sa.String(length=3), nullable=False),
        sa.Column("tax_amount_charge", sa.Numeric(precision=20, scale=4), nullable=False),
        sa.Column("charge_currency", sa.String(length=3), nullable=False),
        sa.Column("is_exempt", sa.Boolean(), nullable=False),
        sa.Column("is_reverse_charge", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['payment_id'], ['pay_payments.id']),
    )
    op.create_index("ix_pay_tax_transactions_created_at", "pay_tax_transactions", ['created_at'], unique=False)
    op.create_index("ix_pay_tax_transactions_payment_id", "pay_tax_transactions", ['payment_id'], unique=False)
    op.create_index("ix_pay_tax_transactions_refund_id", "pay_tax_transactions", ['refund_id'], unique=False)
    op.create_index("ix_pay_tax_transactions_tenant_id", "pay_tax_transactions", ['tenant_id'], unique=False)

    now = datetime.utcnow()
    table = sa.table("pay_currencies", sa.column("code", sa.String), sa.column("name", sa.String), sa.column("symbol", sa.String), sa.column("decimals", sa.Integer),
                     sa.column("is_active", sa.Boolean), sa.column("sort_order", sa.Integer), sa.column("created_at", sa.DateTime))
    op.bulk_insert(table, [
        {"code": code, "name": name, "symbol": symbol, "decimals": decimals, "is_active": active, "sort_order": order, "created_at": now}
        for order, (code, name, symbol, decimals, active) in enumerate(CURRENCIES)
    ])


def downgrade():
    op.drop_table("pay_tax_transactions")
    op.drop_table("pay_settlement_items")
    op.drop_table("pay_refunds")
    op.drop_table("pay_reconciliation_items")
    op.drop_table("pay_payment_transactions")
    op.drop_table("pay_invoices")
    op.drop_table("pay_webhook_events")
    op.drop_table("pay_tax_rules")
    op.drop_table("pay_tax_exemptions")
    op.drop_table("pay_settlements")
    op.drop_table("pay_settlement_configs")
    op.drop_table("pay_saved_methods")
    op.drop_table("pay_reconciliation_runs")
    op.drop_table("pay_payments")
    op.drop_table("pay_merchant_tax_profiles")
    op.drop_table("pay_invoice_templates")
    op.drop_table("pay_invoice_sequences")
    op.drop_table("pay_exchange_rates")
    op.drop_table("pay_currency_preferences")
    op.drop_table("pay_currencies")
    op.drop_table("pay_audit_log")
