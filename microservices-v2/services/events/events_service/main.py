"""Entry point of the events service (v2: own code, own database).

Run:  uvicorn events_service.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'

Events, event categories and types, event media, discussions, event form configuration and the common (event + training) form contract.
"""
import importlib

from mskit.app import create_service_app
from mskit.settings import settings

from events_service import model_registry  # noqa: F401 - registers the tables
from events_service.db.database import Base, database, engine

# (position in the monolith's app/api/v1/router.py, module, router attribute, prefix, tags)
MOUNTS = (
    (12, 'event_media', 'router', '/events/media', ('Event Media',)),
    (13, 'form_common', 'router', '', None),
    (14, 'event_discussion', 'router', '/events/{event_id}/discussion', ('Event Discussions',)),
    (15, 'event_discussion', 'overview_router', '/event-discussions', ('Event Discussions',)),
    (16, 'event', 'router', '/events', ('Events',)),
    (17, 'event_category', 'router', '/event-categories', ('Event Categories',)),
    (18, 'event_type', 'router', '/event-types', ('Event Types',)),
    (59, 'event_form_config_admin', 'router', '/events/form-configuration/admin', ('Event Form Configuration (Super Admin)',)),
    (60, 'event_form_config_admin', 'router', '/admin/event-form-configurations', ('Event Form Configuration (Super Admin)',)),
    (38, 'search_events', 'router', '/search', ('Search',)),
    (58, 'admin_events', 'router', '/admin', ('Admin — Approvals',)),
)


def _routers():
    out = []
    for _order, module, attr, prefix, tags in sorted(MOUNTS):
        mod = importlib.import_module(f"events_service.api.v1.endpoints.{module}")
        out.append((getattr(mod, attr), prefix, tags))
    return out


def _internal_routers():
    try:
        mod = importlib.import_module("events_service.internal_api")
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
        hooks += list(importlib.import_module("events_service.startup").HOOKS)
    except ModuleNotFoundError:
        pass
    return hooks


def _asgi_wrapper():
    try:
        return importlib.import_module("events_service.asgi").wrap
    except ModuleNotFoundError:
        return None


app, fastapi_app = create_service_app(
    name='events',
    public_routers=_routers(),
    internal_routers=_internal_routers(),
    database=database,
    on_startup=_startup_hooks(),
    description='Events, event categories and types, event media, discussions, event form configuration and the common (event + training) form contract.',
    asgi_wrapper=_asgi_wrapper(),
)
