"""Start-up of the communication service.

* the event loop for emits from sync code and background threads (monolith: app/main.py startup);
* the room-sync listener (group room joins/leaves published by other processes);
* the group data export job;
* read-only replicas (design D5): catalog's enterprises/locations, events' events/registrations/orders and
  learning's trainings/enrolments/programs, which the group chats read.
"""
import asyncio

from mskit.jobs import job
from mskit.replication import Subscriber

from communication_service.db.database import SessionLocal
from communication_service.models.enterprise_model import Enterprise
from communication_service.models.event_aux_models import EventOrder, EventRegistration
from communication_service.models.event_model import Event
from communication_service.models.location_model import EnterpriseLocation
from communication_service.models.program_model import Program
from communication_service.models.training_model import Training, TrainingEnrolment

CATALOG = Subscriber("catalog", {"catalog.enterprises": Enterprise, "catalog.enterprise_locations": EnterpriseLocation}, SessionLocal)
EVENTS = Subscriber("events", {"events.events": Event, "events.event_registrations": EventRegistration,
                               "events.event_orders": EventOrder}, SessionLocal)
LEARNING = Subscriber("learning", {"learning.trainings": Training, "learning.training_enrolments": TrainingEnrolment,
                                   "learning.programs": Program}, SessionLocal)
REPLICAS = (CATALOG, EVENTS, LEARNING)
for _r in REPLICAS:
    _r.guard()


def _attach_loop():
    from communication_service.realtime.loop_bridge import set_main_loop

    set_main_loop(asyncio.get_running_loop())


def _room_sync():
    from communication_service.realtime.room_sync import start_listener

    start_listener()


HOOKS = [_attach_loop, _room_sync, job("communication_service.services.group_data_export:start_scheduler"),
         *[r.start_background_sync for r in REPLICAS]]
