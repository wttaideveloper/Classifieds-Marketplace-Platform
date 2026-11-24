"""Start-up of the learning service: read-only replicas of catalog's enterprises and locations."""
from mskit.replication import Subscriber

from learning_service.db.database import SessionLocal
from learning_service.models.enterprise_model import Enterprise
from learning_service.models.location_model import EnterpriseLocation

REPLICAS = Subscriber("catalog", {"catalog.enterprises": Enterprise, "catalog.enterprise_locations": EnterpriseLocation}, SessionLocal)
REPLICAS.guard()  # domain code may read the replicas, never write them

from mskit.jobs import job  # noqa: E402

HOOKS = [REPLICAS.start_background_sync, job("learning_service.services.training_notifications:start_reminder_scheduler")]


# ---------------------------------------------------------------- learning data read by communication (group chats, D5)
from mskit.replication import Publisher  # noqa: E402

from learning_service.models.program_model import Program  # noqa: E402
from learning_service.models.training_model import Training, TrainingEnrolment  # noqa: E402

LEARNING_PUBLISHER = Publisher({"learning.trainings": Training, "learning.training_enrolments": TrainingEnrolment,
                                "learning.programs": Program}, subscribers_env="LEARNING_REPLICA_SUBSCRIBERS")
LEARNING_PUBLISHER.install()
