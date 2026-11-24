"""Internal API of the events service (/internal/v1, shared key, never routed by the gateway)."""
from fastapi import APIRouter, Depends

from events_service.startup import REPLICAS
from mskit.app import require_internal_key
from mskit.replication import subscriber_router

router = APIRouter(dependencies=[Depends(require_internal_key)], tags=["internal"])

from events_service.event_order_api import router as event_order_router  # noqa: E402

from events_service.meetings_api import router as meetings_router  # noqa: E402

routers = [router, subscriber_router(REPLICAS), event_order_router, meetings_router]  # replicas (catalog), event orders (commerce, D2), meetings (D7)


# review moderation queue (D6): this service's slice + moderate/delete, called by moderation
from events_service.db.database import get_db as _get_db  # noqa: E402
from events_service.review_queue_adapter import EventReviews  # noqa: E402
from mskit.review_queue import owner_router  # noqa: E402

routers = [*routers, owner_router(EventReviews(), _get_db)]


# replica snapshots of events data + the registrant identity fill communication asks for (D5)
from uuid import UUID as _UUID  # noqa: E402

from fastapi import APIRouter as _APIRouter, Depends as _Depends  # noqa: E402
from pydantic import BaseModel as _BaseModel  # noqa: E402

from events_service.db.database import SessionLocal as _SessionLocal, get_db as _get_db2  # noqa: E402
from events_service.startup import EVENTS_PUBLISHER  # noqa: E402
from mskit.app import require_internal_key as _key  # noqa: E402
from mskit.replication import publisher_router as _publisher_router  # noqa: E402

identity_fill_router = _APIRouter(dependencies=[_Depends(_key)], tags=["internal"])


class _Fill(_BaseModel):
    registration_ids: list[_UUID]
    access_token: str | None = None


@identity_fill_router.post("/events/{event_id}/registrations/resolve-user-ids")
def resolve_registration_user_ids(event_id: _UUID, body: _Fill, db=_Depends(_get_db2)):
    """event_registration_identity.resolve_user_ids on the given rows (it commits; the replica push follows)."""
    from events_service.models.event_aux_models import EventRegistration
    from events_service.models.event_model import Event
    from events_service.services.event_registration_identity import resolve_user_ids

    event = db.get(Event, event_id)
    rows = db.query(EventRegistration).filter(EventRegistration.id.in_(body.registration_ids)).all()
    if event is not None and rows:
        resolve_user_ids(db, rows, event, body.access_token)
    return {str(r.id): (str(r.user_id) if r.user_id else None) for r in rows}


routers = [*routers, _publisher_router(EVENTS_PUBLISHER, _SessionLocal), identity_fill_router]
