"""Imports every model module so Base.metadata lists all of this service's tables."""
import catalog_service.models.attribute_model  # noqa: F401
import catalog_service.models.enterprise_model  # noqa: F401
import catalog_service.models.location_model  # noqa: F401
import catalog_service.models.onboarding_form_model  # noqa: F401
import catalog_service.models.product_model  # noqa: F401
import catalog_service.models.service_model  # noqa: F401

OWNED_TABLES = ['dynamic_attributes', 'enterprise_locations', 'enterprises', 'onboarding_form_fields', 'onboarding_form_sections', 'onboarding_forms', 'product_reviews', 'products', 'service_reviews', 'services']
# read-only copies of another service's reference tables (kept current by mskit.replication)
REPLICA_TABLES = []
# tables that exist only in v2 (outbox, reservations, ...): in this service's new database, not in the monolith
import catalog_service.models.stock_reservation_model  # noqa: F401,E402
import catalog_service.stock_api  # noqa: F401,E402
NEW_TABLES = ['outbox_messages', 'processed_messages', 'stock_reservations']
