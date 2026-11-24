"""Start-up of the events service: read-only replicas of catalog's enterprises and locations."""
from mskit.replication import Subscriber

from events_service.db.database import SessionLocal
from events_service.models.enterprise_model import Enterprise
from events_service.models.location_model import EnterpriseLocation

REPLICAS = Subscriber("catalog", {"catalog.enterprises": Enterprise, "catalog.enterprise_locations": EnterpriseLocation}, SessionLocal)
REPLICAS.guard()  # domain code may read the replicas, never write them

from mskit.jobs import job  # noqa: E402

HOOKS = [REPLICAS.start_background_sync, job("events_service.services.event_discussion_jobs:start_scheduler")]


# ---------------------------------------------------------------- events data read by communication (group chats, D5)
from mskit.replication import Publisher  # noqa: E402

from events_service.models.event_aux_models import EventOrder, EventRegistration  # noqa: E402
from events_service.models.event_model import Event  # noqa: E402

EVENTS_PUBLISHER = Publisher({"events.events": Event, "events.event_registrations": EventRegistration,
                              "events.event_orders": EventOrder}, subscribers_env="EVENTS_REPLICA_SUBSCRIBERS")
EVENTS_PUBLISHER.install()
