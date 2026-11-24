"""Behaviour of the moderation service vs. the monolith: every operation the gateway routes here, anonymous and
as each role, with generated requests; responses and the service's own tables compared.
Other services are replaced by stubs that behave like an empty domain (see tests/stubs.py)."""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

V2 = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(V2 / "tests" / "support"))

from moderation_service import main  # noqa: E402
from moderation_service.db.database import engine as service_engine  # noqa: E402
from moderation_service.model_registry import OWNED_TABLES  # noqa: E402


def test_same_behaviour_as_monolith(tmp_path, stubs):
    from compare import compare_service

    from app.db.database import engine as monolith_engine
    from app.main import app as monolith_app

    # unhandled errors must come back as the 500 response a client sees, not as a test exception
    with TestClient(main.app, raise_server_exceptions=False) as svc:
        report = compare_service("moderation", TestClient(monolith_app, raise_server_exceptions=False), svc, monolith_engine, service_engine, OWNED_TABLES)
    (Path(__file__).parent / "behaviour_report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    assert report["operations"] > 0
    assert not report["differences"], json.dumps(report["differences"][:5], indent=1, default=str)
    assert not report["db_differences"], json.dumps(report["db_differences"][:5], indent=1, default=str)
