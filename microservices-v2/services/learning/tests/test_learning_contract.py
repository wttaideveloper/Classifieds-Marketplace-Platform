"""Contract tests of the learning service (v2): API surface, code independence, schema, behaviour."""
import ast
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from learning_service import main
from learning_service.db.database import Base
from learning_service.model_registry import NEW_TABLES, OWNED_TABLES, REPLICA_TABLES

SERVICE = "learning"
SERVICE_DIR = Path(__file__).resolve().parents[1]
V2 = SERVICE_DIR.parents[1]
ROUTES = json.loads((V2 / "gateway" / "gateway_app" / "routes.json").read_text(encoding="utf-8"))["routes"]
MINE = [r for r in ROUTES if r["service"] == SERVICE and r["path"].startswith("/api/v1")]


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(scope="module")
def monolith():
    from app.main import app as monolith_app
    return TestClient(monolith_app)


def _flatten(routes):
    try:
        from fastapi.routing import _iter_routes_with_context
        items = [(ctx.path if ctx else getattr(r, "path", None), sorted((ctx.methods if ctx else getattr(r, "methods", None)) or [])) for r, ctx in _iter_routes_with_context(routes)]
    except ImportError:
        items = [(getattr(r, "path", None), sorted(getattr(r, "methods", None) or [])) for r in routes]
    return [(p, m) for p, m in items if p and p.startswith("/api/v1")]


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/_service/ready").json()["checks"]["database"] == "ok"


def test_public_routes_are_exactly_this_services_routes_in_monolith_order():
    assert _flatten(main.fastapi_app.routes) == [(r["path"], r["methods"]) for r in MINE]


def test_no_code_from_the_monolith_or_other_services():
    others = {p.name + "_service" for p in (V2 / "services").iterdir() if p.is_dir() and p.name != SERVICE}
    bad = []
    for py in (SERVICE_DIR / f"{SERVICE}_service").rglob("*.py"):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
            for m in mods:
                if m == "app" or m.startswith("app.") or m.split(".")[0] in others:
                    bad.append(f"{py.relative_to(SERVICE_DIR)}: {m}")
    assert not bad, bad


def test_owns_only_its_tables():
    assert sorted(Base.metadata.tables) == sorted(OWNED_TABLES + REPLICA_TABLES + NEW_TABLES)


def _columns(table):
    from sqlalchemy.dialects import postgresql
    d = postgresql.dialect()
    return {c.name: (c.type.compile(dialect=d), c.nullable, c.primary_key) for c in table.columns}


def test_schema_is_identical_to_the_monolith():
    from app.db.database import Base as MonolithBase
    for name in OWNED_TABLES + REPLICA_TABLES:
        mine, theirs = Base.metadata.tables[name], MonolithBase.metadata.tables[name]
        assert _columns(mine) == _columns(theirs), name
        assert sorted(i.name for i in mine.indexes) == sorted(i.name for i in theirs.indexes), name


def test_anonymous_parameter_free_gets_match_the_monolith(client, monolith):
    mismatches = []
    for r in MINE:
        if "GET" not in r["methods"] or "{" in r["path"]:
            continue
        a, b = monolith.get(r["path"]), client.get(r["path"])
        if a.status_code != b.status_code or (a.status_code >= 400 and a.content != b.content):
            mismatches.append((r["path"], a.status_code, b.status_code, a.text[:150], b.text[:150]))
    assert not mismatches, mismatches
