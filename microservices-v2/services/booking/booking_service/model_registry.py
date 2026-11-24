"""Imports every model module so Base.metadata lists all of this service's tables."""
import booking_service.models.service_booking_model  # noqa: F401
import booking_service.models.enterprise_model  # noqa: F401
import booking_service.models.location_model  # noqa: F401
import booking_service.models.service_model  # noqa: F401

OWNED_TABLES = ['service_booking_events', 'service_bookings']
# read-only copies of another service's reference tables (kept current by mskit.replication)
REPLICA_TABLES = ['enterprise_locations', 'enterprises', 'services']
# tables that exist only in v2 (outbox, reservations, ...): in this service's new database, not in the monolith
NEW_TABLES = []
