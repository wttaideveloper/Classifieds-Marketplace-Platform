"""Imports every model module so Base.metadata lists all of this service's tables."""
import notifications_service.models.notification_model  # noqa: F401

OWNED_TABLES = ['notification_event_log', 'notification_logs', 'notification_templates', 'notifications', 'user_notifications']
# read-only copies of another service's reference tables (kept current by mskit.replication)
REPLICA_TABLES = []
# tables that exist only in v2 (outbox, reservations, ...): in this service's new database, not in the monolith
NEW_TABLES = []
