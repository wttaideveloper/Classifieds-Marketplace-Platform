"""Entry point of the booking service (v2: own code, own database).

Run:  uvicorn booking_service.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'

Service bookings: availability slots, booking settings, bookings, holds and reminders.
"""
import importlib

from mskit.app import create_service_app
from mskit.settings import settings

from booking_service import model_registry  # noqa: F401 - registers the tables
from booking_service.db.database import Base, database, engine

# (position in the monolith's app/api/v1/router.py, module, router attribute, prefix, tags)
MOUNTS = (
    (6, 'service_booking', 'service_router', '/services', ('Service Bookings',)),
    (7, 'service_booking', 'router', '/service-bookings', ('Service Bookings',)),
    (8, 'service_booking', 'me_router', '/users/me/service-bookings', ('Service Bookings',)),
    (9, 'service_booking', 'enterprise_router', '/enterprises', ('Service Bookings',)),
)


def _routers():
    out = []
    for _order, module, attr, prefix, tags in sorted(MOUNTS):
        mod = importlib.import_module(f"booking_service.api.v1.endpoints.{module}")
        out.append((getattr(mod, attr), prefix, tags))
    return out


def _internal_routers():
    try:
        mod = importlib.import_module("booking_service.internal_api")
    except ModuleNotFoundError:
        return []
    return list(getattr(mod, "routers", None) or [mod.router])


def _create_tables():
    # Same switch as the monolith: development only, AUTO_CREATE_TABLES=true. Creates THIS service's tables only.
    if not settings.is_production and settings.AUTO_CREATE_TABLES:
        Base.metadata.create_all(bind=engine)


def _startup_hooks():
    hooks = [_create_tables]
    try:
        hooks += list(importlib.import_module("booking_service.startup").HOOKS)
    except ModuleNotFoundError:
        pass
    return hooks


def _asgi_wrapper():
    try:
        return importlib.import_module("booking_service.asgi").wrap
    except ModuleNotFoundError:
        return None


app, fastapi_app = create_service_app(
    name='booking',
    public_routers=_routers(),
    internal_routers=_internal_routers(),
    database=database,
    on_startup=_startup_hooks(),
    description='Service bookings: availability slots, booking settings, bookings, holds and reminders.',
    asgi_wrapper=_asgi_wrapper(),
)
