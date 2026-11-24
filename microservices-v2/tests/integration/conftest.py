"""Cross-service integration: several v2 services behind the real v2 gateway, in one process, each on its
own SQLite database, internal API calls between them routed for real; the monolith on its own database as
the reference. Services not under test are the empty-domain stubs (tests/support/stubs.py)."""
import os
import socketserver
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

V2 = Path(__file__).resolve().parents[2]
REPO = V2.parent
SERVICES_UNDER_TEST = ("catalog", "events", "learning", "commerce", "booking", "meetings", "communication", "moderation", "notifications", "identity", "content")
for p in [str(V2 / "services" / s) for s in SERVICES_UNDER_TEST] + [str(V2 / "gateway"), str(V2 / "kit"), str(V2 / "tests" / "support"), str(REPO)]:
    if p not in sys.path:
        sys.path.insert(0, p)

TMP = Path(tempfile.mkdtemp(prefix="v2-integration-"))


class _Unavailable(BaseHTTPRequestHandler):
    def _answer(self):
        self.send_response(503)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _answer

    def log_message(self, *a):
        pass


class _CloseAtOnce(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.close()


_HTTP = ThreadingHTTPServer(("127.0.0.1", 0), _Unavailable)
_SMTP = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _CloseAtOnce)
for srv in (_HTTP, _SMTP):
    threading.Thread(target=srv.serve_forever, daemon=True).start()
EXT = f"http://127.0.0.1:{_HTTP.server_address[1]}"


def _sqlite(name):
    return "sqlite:///" + str(TMP / name).replace("\\", "/")


os.environ.update({
    "ENVIRONMENT": "development", "AUTO_CREATE_TABLES": "false", "SECRET_KEY": "v2-test-secret",
    "GOOGLE_CLIENT_ID": "x", "GOOGLE_CLIENT_SECRET": "x", "GOOGLE_REDIRECT_URI": "http://localhost/x",
    "email_user": "test@example.invalid", "email_pass": "x", "SMTP_HOST": "127.0.0.1", "SMTP_PORT": str(_SMTP.server_address[1]),
    "KEYCLOAK_ISSUER": "", "KEYCLOAK_AUDIENCE": "", "KEYCLOAK_JWKS_URL": "",
    "INVIGORATE_AUTH_BASE_URL": EXT, "INVIGORATE_ADMIN_API_BASE_URL": EXT, "INVIGORATE_INTERNAL_API_KEY": "",
    "SOCKETIO_REDIS_URL": "", "FIREBASE_CREDENTIALS_PATH": "", "FIREBASE_CREDENTIALS_JSON": "",
    "BRAVO_SMS_API_URL": "", "BRAVO_API_KEY": "", "OPENAI_API_KEY": "", "PAYMENT_GATEWAY": "test", "ENABLE_DEV_TOKEN": "false",
    "UPLOAD_DIR": str(TMP / "uploads"), "LOG_LEVEL": "WARNING", "DISABLE_BACKGROUND_JOBS": "true",
    "INTERNAL_API_KEY": "v2-internal-test-key", "CELERY_BROKER_URL": "memory://", "CELERY_RESULT_BACKEND": "cache+memory://",
    "REPLICA_SUBSCRIBERS": "events,learning,commerce,booking,communication",
    "EVENTS_REPLICA_SUBSCRIBERS": "communication", "LEARNING_REPLICA_SUBSCRIBERS": "communication",
})

from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(_type, _compiler, **_kw):
    return "JSON"


# monolith (reference) first: its settings bind to its own file
os.environ["DATABASE_URL"] = _sqlite("monolith.db")
import app.main as _monolith  # noqa: E402,F401
from app.db.database import Base as _MB, engine as _ME  # noqa: E402

_MB.metadata.create_all(_ME)

os.environ["DATABASE_URL"] = _sqlite("unused.db")
for svc in SERVICES_UNDER_TEST:
    os.environ[f"{svc.upper()}_DATABASE_URL"] = _sqlite(f"{svc}.db")

import importlib  # noqa: E402

APPS = {}
for svc in SERVICES_UNDER_TEST:
    importlib.import_module(f"{svc}_service.model_registry")
    db = importlib.import_module(f"{svc}_service.db.database")
    db.Base.metadata.create_all(db.engine)
    APPS[svc] = importlib.import_module(f"{svc}_service.main")


@pytest.fixture(scope="session")
def stack():
    """(monolith client, gateway client, {service: TestClient}) with internal calls routed for real."""
    import httpx
    from fastapi.testclient import TestClient

    import stubs
    from gateway_app.config import SERVICE_NAMES, GatewaySettings
    from gateway_app.main import create_app
    from mskit import clients, internal
    from mskit.http_client import CallPolicy

    direct = {svc: TestClient(APPS[svc].app) for svc in SERVICES_UNDER_TEST}

    def routed_call(service, method, path, *, json=None, params=None, timeout=None, expect=(200, 201, 202, 204, 404), headers=None):
        if service in direct:
            r = direct[service].request(method, "/internal/v1" + path, json=json, params=params,
                                        headers={**(headers or {}), "X-Internal-Api-Key": os.environ["INTERNAL_API_KEY"]})
            stubs.CALLS.append((service, method.upper(), path, json))
            if r.status_code not in expect:
                raise internal.ServiceError(r.status_code, r.text)
            return r
        return stubs.fake_call(service, method, path, json=json, params=params, timeout=timeout, expect=expect, headers=headers)

    internal.call = routed_call
    clients.call = routed_call
    from mskit.auth import tenant_lookup

    tenant_lookup._remote = lambda enterprise_id: (lambda r: r.json().get("tenant_id") if r.status_code == 200 else None)(
        routed_call("catalog", "GET", f"/enterprises/{enterprise_id}/tenant"))

    def down(request):
        return httpx.Response(503, json={"detail": "service not part of this test"})

    transports = {n: httpx.MockTransport(down) for n in SERVICE_NAMES}
    for svc in SERVICES_UNDER_TEST:
        transports[svc] = httpx.ASGITransport(app=APPS[svc].app, raise_app_exceptions=False)  # a crash is a 500, as with a real server
    gw = create_app(GatewaySettings(service_urls={n: f"http://{n}" for n in SERVICE_NAMES}, policy=CallPolicy(retry_backoff=0.0)), transports=transports)
    from app.main import app as monolith_app

    with TestClient(gw, raise_server_exceptions=False) as gateway:
        yield TestClient(monolith_app, raise_server_exceptions=False), gateway, direct
