"""Imports every model module so Base.metadata lists all of this service's tables."""
import commerce_service.models.cart_model  # noqa: F401
import commerce_service.models.payment_model  # noqa: F401
import commerce_service.models.enterprise_model  # noqa: F401
import commerce_service.models.location_model  # noqa: F401
import commerce_service.models.product_model  # noqa: F401

OWNED_TABLES = ['cart_items', 'carts', 'order_items', 'order_status_events', 'orders', 'pay_audit_log', 'pay_currencies', 'pay_currency_preferences', 'pay_exchange_rates', 'pay_invoice_sequences', 'pay_invoice_templates', 'pay_invoices', 'pay_merchant_tax_profiles', 'pay_payment_transactions', 'pay_payments', 'pay_reconciliation_items', 'pay_reconciliation_runs', 'pay_refunds', 'pay_saved_methods', 'pay_settlement_configs', 'pay_settlement_items', 'pay_settlements', 'pay_tax_exemptions', 'pay_tax_rules', 'pay_tax_transactions', 'pay_webhook_events']
# read-only copies of another service's reference tables (kept current by mskit.replication)
REPLICA_TABLES = ['enterprise_locations', 'enterprises', 'products']
# tables that exist only in v2 (outbox, reservations, ...): in this service's new database, not in the monolith
import commerce_service.outbox_setup  # noqa: F401,E402
NEW_TABLES = ['outbox_messages', 'processed_messages']
