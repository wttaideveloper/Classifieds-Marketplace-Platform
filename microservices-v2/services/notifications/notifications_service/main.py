"""Entry point of the notifications service (v2: own code, own database).

Run:  uvicorn notifications_service.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'

Platform notifications, per-user notification settings/inbox and notification templates.
"""
import importlib

from mskit.app import create_service_app
from mskit.settings import settings

from notifications_service import model_registry  # noqa: F401 - registers the tables
from notifications_service.db.database import Base, database, engine

# (position in the monolith's app/api/v1/router.py, module, router attribute, prefix, tags)
MOUNTS = (
    (49, 'platform_notification', 'router', '/notifications', ('Platform Notifications',)),
    (50, 'user_notification', 'router', '/users', ('User Notifications',)),
    (51, 'notification_template', 'router', '/notification-templates', ('Notification Templates',)),
)


def _routers():
    out = []
    for _order, module, attr, prefix, tags in sorted(MOUNTS):
        mod = importlib.import_module(f"notifications_service.api.v1.endpoints.{module}")
        out.append((getattr(mod, attr), prefix, tags))
    return out


def _internal_routers():
    try:
        mod = importlib.import_module("notifications_service.internal_api")
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
        hooks += list(importlib.import_module("notifications_service.startup").HOOKS)
    except ModuleNotFoundError:
        pass
    return hooks


def _asgi_wrapper():
    try:
        return importlib.import_module("notifications_service.asgi").wrap
    except ModuleNotFoundError:
        return None


app, fastapi_app = create_service_app(
    name='notifications',
    public_routers=_routers(),
    internal_routers=_internal_routers(),
    database=database,
    on_startup=_startup_hooks(),
    description='Platform notifications, per-user notification settings/inbox and notification templates.',
    asgi_wrapper=_asgi_wrapper(),
)
