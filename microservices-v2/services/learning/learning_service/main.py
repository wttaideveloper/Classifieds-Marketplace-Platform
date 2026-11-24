"""Entry point of the learning service (v2: own code, own database).

Run:  uvicorn learning_service.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'

Trainings (also served as /courses), training categories, programs, eBooks, learning media, form configuration for trainings and programs, and the learner's own trainings (/me).
"""
import importlib

from mskit.app import create_service_app
from mskit.settings import settings

from learning_service import model_registry  # noqa: F401 - registers the tables
from learning_service.db.database import Base, database, engine

# (position in the monolith's app/api/v1/router.py, module, router attribute, prefix, tags)
MOUNTS = (
    (19, 'training', 'router', '/trainings', ('Trainings',)),
    (20, 'training_category', 'router', '/training-categories', ('Training Categories',)),
    (21, 'training', 'router', '/courses', ('Courses',)),
    (22, 'program', 'router', '/programs', ('Programs',)),
    (23, 'ebook', 'router', '/trainings', ('eBooks',)),
    (24, 'ebook', 'router', '/courses', ('eBooks',)),
    (25, 'ebook', 'admin_router', '/admin', ('eBooks (Super Admin review)',)),
    (26, 'media', 'router', '/media', ('Media',)),
    (61, 'training_form_config_admin', 'router', '/trainings/form-configuration/admin', ('Training Form Configuration (Super Admin)',)),
    (62, 'training_form_config_admin', 'router', '/admin/training-form-configurations', ('Training Form Configuration (Super Admin)',)),
    (63, 'program_form_config_admin', 'router', '/programs/form-configuration/admin', ('Program Form Configuration (Super Admin)',)),
    (64, 'program_form_config_admin', 'router', '/admin/program-form-configurations', ('Program Form Configuration (Super Admin)',)),
    (69, 'my_trainings', 'router', '/me', ('Trainings',)),
    (38, 'search_learning', 'router', '/search', ('Search',)),
    (13, 'form_common_training', 'router', '', None),
    (58, 'admin_learning', 'router', '/admin', ('Admin — Approvals',)),
)


def _routers():
    out = []
    for _order, module, attr, prefix, tags in sorted(MOUNTS):
        mod = importlib.import_module(f"learning_service.api.v1.endpoints.{module}")
        out.append((getattr(mod, attr), prefix, tags))
    return out


def _internal_routers():
    try:
        mod = importlib.import_module("learning_service.internal_api")
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
        hooks += list(importlib.import_module("learning_service.startup").HOOKS)
    except ModuleNotFoundError:
        pass
    return hooks


def _asgi_wrapper():
    try:
        return importlib.import_module("learning_service.asgi").wrap
    except ModuleNotFoundError:
        return None


app, fastapi_app = create_service_app(
    name='learning',
    public_routers=_routers(),
    internal_routers=_internal_routers(),
    database=database,
    on_startup=_startup_hooks(),
    description="Trainings (also served as /courses), training categories, programs, eBooks, learning media, form configuration for trainings and programs, and the learner's own trainings (/me).",
    asgi_wrapper=_asgi_wrapper(),
)
