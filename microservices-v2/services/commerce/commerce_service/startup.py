"""Start-up of the commerce service: replicas of catalog's enterprises, locations and products; the outbox relay."""
from mskit.replication import Subscriber

from commerce_service.db.database import SessionLocal
from commerce_service.models.enterprise_model import Enterprise
from commerce_service.models.location_model import EnterpriseLocation
from commerce_service.models.product_model import Product
from commerce_service.outbox_setup import OUTBOX

REPLICAS = Subscriber("catalog", {"catalog.enterprises": Enterprise, "catalog.enterprise_locations": EnterpriseLocation,
                                  "catalog.products": Product}, SessionLocal)
REPLICAS.guard()

from mskit.jobs import job  # noqa: E402

HOOKS = [REPLICAS.start_background_sync, OUTBOX.start_relay, job("commerce_service.services.payment_jobs:start_scheduler")]
