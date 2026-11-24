"""Entry point of the communication service (v2: own code, own database).

Run:  uvicorn communication_service.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'

Chat: conversations, groups, messages, attachments, typing, presence, devices, chat notifications, provider assignment, subscriptions, chat admin and the Socket.IO real-time server.
"""
import importlib

from mskit.app import create_service_app
from mskit.settings import settings

from communication_service import model_registry  # noqa: F401 - registers the tables
from communication_service.db.database import Base, database, engine

# (position in the monolith's app/api/v1/router.py, module, router attribute, prefix, tags)
MOUNTS = (
    (41, 'conversation', 'router', '/conversations', ('Conversations',)),
    (42, 'group_chat', 'router', '/groups', ('Group Chat',)),
    (43, 'conversation_messages', 'router', '/conversations', ('Conversations',)),
    (44, 'typing_indicator', 'router', '/conversations', ('Typing Indicator',)),
    (45, 'message', 'router', '/messages', ('Messages',)),
    (46, 'attachment', 'router', '/attachments', ('Attachments',)),
    (48, 'chat_notification', 'router', '/notifications', ('Chat Notifications',)),
    (52, 'device', 'router', '/devices', ('Devices',)),
    (53, 'provider_assignment', 'router', '/providers', ('Provider Assignment',)),
    (54, 'chat_subscription', 'router', '/subscriptions', ('Chat Subscriptions',)),
    (55, 'presence', 'router', '/presence', ('Presence',)),
    (56, 'socket_io', 'router', '/socket-io', ('Socket.IO',)),
    (57, 'chat_admin', 'router', '/admin/chat', ('Chat Administration',)),
)


def _routers():
    out = []
    for _order, module, attr, prefix, tags in sorted(MOUNTS):
        mod = importlib.import_module(f"communication_service.api.v1.endpoints.{module}")
        out.append((getattr(mod, attr), prefix, tags))
    return out


def _internal_routers():
    try:
        mod = importlib.import_module("communication_service.internal_api")
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
        hooks += list(importlib.import_module("communication_service.startup").HOOKS)
    except ModuleNotFoundError:
        pass
    return hooks


def _asgi_wrapper():
    try:
        return importlib.import_module("communication_service.asgi").wrap
    except ModuleNotFoundError:
        return None


app, fastapi_app = create_service_app(
    name='communication',
    public_routers=_routers(),
    internal_routers=_internal_routers(),
    database=database,
    on_startup=_startup_hooks(),
    description='Chat: conversations, groups, messages, attachments, typing, presence, devices, chat notifications, provider assignment, subscriptions, chat admin and the Socket.IO real-time server.',
    asgi_wrapper=_asgi_wrapper(),
)
