"""Start-up of the booking service: read-only replicas of catalog's enterprises, locations and services (D4)."""
from mskit.replication import Subscriber

from booking_service.db.database import SessionLocal
from booking_service.models.enterprise_model import Enterprise
from booking_service.models.location_model import EnterpriseLocation
from booking_service.models.service_model import Service

REPLICAS = Subscriber("catalog", {"catalog.enterprises": Enterprise, "catalog.enterprise_locations": EnterpriseLocation,
                                  "catalog.services": Service}, SessionLocal)
REPLICAS.guard()

from mskit.jobs import job  # noqa: E402

HOOKS = [REPLICAS.start_background_sync, job("booking_service.services.service_booking_notifications:start_scheduler")]
