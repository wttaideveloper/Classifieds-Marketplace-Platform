"""Generate the standard files of a v2 service (never overwrites unless --force).

    python microservices-v2/tools/scaffold.py identity

Inputs: ``services/<name>/extract.json`` (owned files + tables) and ``tools/ownership.json``
(which monolith router mounts the service serves, in monolith order).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

V2 = Path(__file__).resolve().parents[1]
OWNERSHIP = json.loads((V2 / "tools" / "ownership.json").read_text(encoding="utf-8"))


def write(path: Path, text: str, force: bool) -> None:
    if path.exists() and not force:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("service")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    name, force = a.service, a.force
    pkg = f"{name}_service"
    sdir = V2 / "services" / name
    manifest = json.loads((sdir / "extract.json").read_text(encoding="utf-8"))
    info = OWNERSHIP["services"][name]
    mounts = [m for m in OWNERSHIP["mounts"] if m["service"] == name]
    model_modules = [f[:-3].replace("/", ".") for f in manifest["files"] + manifest.get("replicas", []) if f.startswith("models/")]

    write(sdir / pkg / "db" / "__init__.py", "", force)
    name_upper = name.upper()
    write(sdir / pkg / "db" / "database.py", f'''"""The {name} service's own database (DATABASE_URL) and declarative Base."""
import os

from sqlalchemy.orm import declarative_base

from mskit.db import Database
from mskit.settings import settings

# {name_upper}_DATABASE_URL wins over DATABASE_URL (several services in one process, e.g. integration tests)
database = Database(os.environ.get("{name_upper}_DATABASE_URL") or settings.DATABASE_URL)
engine = database.engine
SessionLocal = database.SessionLocal
Base = declarative_base()


def get_db():
    yield from database.get_db()
''', force)

    write(sdir / pkg / "model_registry.py", '"""Imports every model module so Base.metadata lists all of this service\'s tables."""\n'
          + "".join(f"import {pkg}.{m}  # noqa: F401\n" for m in model_modules)
          + f"\nOWNED_TABLES = {manifest['tables']!r}\n"
          + "# read-only copies of another service's reference tables (kept current by mskit.replication)\n"
          + f"REPLICA_TABLES = {manifest.get('replica_tables', [])!r}\n"
          + "# tables that exist only in v2 (outbox, reservations, ...): in this service's new database, not in the monolith\n"
          + "".join(f"import {m}  # noqa: F401,E402\n" for m in manifest.get("registry_imports", []))
          + f"NEW_TABLES = {manifest.get('new_tables', [])!r}\n", force)

    mount_lines = "\n".join(
        f"    ({m['order']}, {m.get('v2_module', m['module'])!r}, {m['attr']!r}, {m['prefix']!r}, {tuple(m['tags']) if m['tags'] is not None else None!r}),"
        for m in mounts
    )
    write(sdir / pkg / "main.py", f'''"""Entry point of the {name} service (v2: own code, own database).

Run:  uvicorn {pkg}.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'

{info["description"]}
"""
import importlib

from mskit.app import create_service_app
from mskit.settings import settings

from {pkg} import model_registry  # noqa: F401 - registers the tables
from {pkg}.db.database import Base, database, engine

# (position in the monolith's app/api/v1/router.py, module, router attribute, prefix, tags)
MOUNTS = (
{mount_lines}
)


def _routers():
    out = []
    for _order, module, attr, prefix, tags in sorted(MOUNTS):
        mod = importlib.import_module(f"{pkg}.api.v1.endpoints.{{module}}")
        out.append((getattr(mod, attr), prefix, tags))
    return out


def _internal_routers():
    try:
        mod = importlib.import_module("{pkg}.internal_api")
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
        hooks += list(importlib.import_module("{pkg}.startup").HOOKS)
    except ModuleNotFoundError:
        pass
    return hooks


def _asgi_wrapper():
    try:
        return importlib.import_module("{pkg}.asgi").wrap
    except ModuleNotFoundError:
        return None


app, fastapi_app = create_service_app(
    name={name!r},
    public_routers=_routers(),
    internal_routers=_internal_routers(),
    database=database,
    on_startup=_startup_hooks(),
    description={info["description"]!r},
    asgi_wrapper=_asgi_wrapper(),
)
''', force)

    write(sdir / pkg / "copy_data.py", f'''"""Copy this service's tables from the monolith database (dry run unless --execute).

    python -m {pkg}.copy_data --source-url postgresql://user@host/marketplace_db [--execute]
"""
import sys

from mskit.datacopy import cli
from mskit.settings import settings

from {pkg}.db.database import Base
from {pkg}.model_registry import OWNED_TABLES, REPLICA_TABLES

if __name__ == "__main__":
    # own tables + the initial fill of replica tables (afterwards kept current by the owner's pushes/sync)
    sys.exit(cli(Base.metadata, OWNED_TABLES + REPLICA_TABLES, settings.DATABASE_URL))  # NEW_TABLES start empty
''', force)

    write(sdir / pkg / "create_schema.py", f'''"""Create this service's tables in a NEW, empty database (its own DATABASE_URL).

    python -m {pkg}.create_schema
"""
from {pkg} import model_registry  # noqa: F401
from {pkg}.db.database import Base, engine

if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    print("created:", sorted(Base.metadata.tables))
''', force)

    write(sdir / "requirements.txt", f'''# {name} service (v2). The kit (microservices-v2/kit) is pip-installed separately.
-r ../../requirements-base.txt
''', force)
    write(sdir / ".env.example", f'''# {name} service - example values only (no real credentials).
ENVIRONMENT=development
LOG_LEVEL=INFO
# This service's OWN database (not the monolith's)
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/{name}_db
AUTO_CREATE_TABLES=false
SECRET_KEY=change-me-same-value-in-every-service
GOOGLE_CLIENT_ID=placeholder
GOOGLE_CLIENT_SECRET=placeholder
GOOGLE_REDIRECT_URI=http://localhost:8000/placeholder
email_user=placeholder@example.invalid
email_pass=placeholder
INTERNAL_API_KEY=change-me-shared-between-services
CATALOG_SERVICE_URL=http://localhost:8102
''', force)
    write(sdir / "Dockerfile", f'''# docker build -f microservices-v2/services/{name}/Dockerfile -t marketplace-v2-{name} .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PYTHONPATH=/srv/service UPLOAD_DIR=/app/uploads
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && rm -rf /var/lib/apt/lists/*
WORKDIR /srv/service
COPY microservices-v2/requirements-base.txt /srv/requirements-base.txt
RUN pip install --upgrade pip && pip install -r /srv/requirements-base.txt
COPY microservices-v2/kit /srv/kit
RUN pip install /srv/kit
COPY microservices-v2/services/{name}/{pkg} /srv/service/{pkg}
RUN addgroup --system app && adduser --system --ingroup app app && mkdir -p /app/uploads && chown -R app:app /app/uploads
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=5 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/_service/health', timeout=4).status == 200 else 1)"
CMD ["sh", "-c", "exec gunicorn {pkg}.main:app -k uvicorn.workers.UvicornWorker -w ${{WEB_CONCURRENCY:-2}} -b 0.0.0.0:8000 --timeout ${{GUNICORN_TIMEOUT:-300}} --forwarded-allow-ips='*'"]
''', force)
    write(sdir / "pytest.ini", "[pytest]\ntestpaths = tests\naddopts = -p no:cacheprovider\n", force)
    write(sdir / "tests" / "__init__.py", "", force)
    write(sdir / "tests" / "conftest.py", (V2 / "tools" / "templates" / "conftest.py.tmpl").read_text(encoding="utf-8").replace("{{svc}}", name), force)
    write(sdir / "tests" / f"test_{name}_contract.py", (V2 / "tools" / "templates" / "test_contract.py.tmpl").read_text(encoding="utf-8").replace("{{svc}}", name), force)
    write(sdir / "tests" / f"test_{name}_vs_monolith.py", (V2 / "tools" / "templates" / "test_behaviour.py.tmpl").read_text(encoding="utf-8").replace("{{svc}}", name), force)
    print(f"scaffolded {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
