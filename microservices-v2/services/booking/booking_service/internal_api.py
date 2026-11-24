"""Internal API of the booking service (/internal/v1, shared key, never routed by the gateway)."""
from fastapi import APIRouter, Depends

from booking_service.startup import REPLICAS
from mskit.app import require_internal_key
from mskit.replication import subscriber_router

router = APIRouter(dependencies=[Depends(require_internal_key)], tags=["internal"])
routers = [router, subscriber_router(REPLICAS)]
