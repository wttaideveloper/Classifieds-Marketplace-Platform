"""Imports every model module so Base.metadata lists all of this service's tables."""
import meetings_service.models.meeting_model  # noqa: F401

OWNED_TABLES = ['meeting_audit', 'meeting_connections', 'meeting_links', 'meeting_merchant_settings', 'meeting_provider_settings']
# read-only copies of another service's reference tables (kept current by mskit.replication)
REPLICA_TABLES = []
# tables that exist only in v2 (outbox, reservations, ...): in this service's new database, not in the monolith
NEW_TABLES = []
