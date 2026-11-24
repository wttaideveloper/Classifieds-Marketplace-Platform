"""Entry point of the catalog service (v2: own code, own database).

Run:  uvicorn catalog_service.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'

Enterprises and their locations, products, services, dynamic attributes, catalog search and onboarding form templates.
"""
import importlib

from mskit.app import create_service_app
from mskit.settings import settings

from catalog_service import model_registry  # noqa: F401 - registers the tables
from catalog_service.db.database import Base, database, engine

# (position in the monolith's app/api/v1/router.py, module, router attribute, prefix, tags)
MOUNTS = (
    (1, 'enterprise', 'router', '/enterprises', ('Enterprise',)),
    (2, 'enterprise_locations', 'router', '/enterprises', ('Enterprise Locations',)),
    (3, 'location', 'router', '/locations', ('Enterprise Locations',)),
    (4, 'product', 'router', '/products', ('Products',)),
    (5, 'service', 'router', '/services', ('Services',)),
    (10, 'attribute', 'router', '/dynamic-attributes', ('Dynamic Attributes',)),
    (11, 'attribute', 'router', '/attributes', ('Dynamic Attributes (Legacy)',)),
    (38, 'search', 'router', '/search', ('Search',)),
    (40, 'onboarding_form', 'router', '/onboarding-forms', ('Onboarding Forms',)),
)


def _routers():
    out = []
    for _order, module, attr, prefix, tags in sorted(MOUNTS):
        mod = importlib.import_module(f"catalog_service.api.v1.endpoints.{module}")
        out.append((getattr(mod, attr), prefix, tags))
    return out


def _internal_routers():
    try:
        mod = importlib.import_module("catalog_service.internal_api")
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
        hooks += list(importlib.import_module("catalog_service.startup").HOOKS)
    except ModuleNotFoundError:
        pass
    return hooks


def _asgi_wrapper():
    try:
        return importlib.import_module("catalog_service.asgi").wrap
    except ModuleNotFoundError:
        return None


app, fastapi_app = create_service_app(
    name='catalog',
    public_routers=_routers(),
    internal_routers=_internal_routers(),
    database=database,
    on_startup=_startup_hooks(),
    description='Enterprises and their locations, products, services, dynamic attributes, catalog search and onboarding form templates.',
    asgi_wrapper=_asgi_wrapper(),
)
