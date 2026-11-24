"""Test setup for the notifications service (v2).

Two isolated SQLite files: one for this service, one for the monolith (used only as the reference
in comparison tests). No network: every external system points at a closed local port.
"""
import os
import sys
import tempfile
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]
V2 = SERVICE_DIR.parents[1]
REPO = V2.parent
for p in (str(SERVICE_DIR), str(V2 / "kit"), str(REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)

_TMP = Path(tempfile.mkdtemp(prefix="notifications-v2-test-"))

# A local stand-in for every external system (Invigorate auth API, SMTP): answers 503 at once.
# Nothing leaves the machine, and calls fail fast (a refused port costs ~2 s per call on Windows).
import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402


class _Unavailable(BaseHTTPRequestHandler):
    def _answer(self):
        self.send_response(503)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _answer

    def log_message(self, *args):
        pass


_EXTERNAL = ThreadingHTTPServer(("127.0.0.1", 0), _Unavailable)
threading.Thread(target=_EXTERNAL.serve_forever, daemon=True).start()
_EXT = f"http://127.0.0.1:{_EXTERNAL.server_address[1]}"

# SMTP stand-in: accepts and closes at once, so smtplib fails immediately (no greeting, no mail sent).
import socketserver  # noqa: E402


class _CloseAtOnce(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.close()


_SMTP = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _CloseAtOnce)
threading.Thread(target=_SMTP.serve_forever, daemon=True).start()
COMMON = {
    "ENVIRONMENT": "development",
    "AUTO_CREATE_TABLES": "false",
    "SECRET_KEY": "v2-test-secret",
    "GOOGLE_CLIENT_ID": "x", "GOOGLE_CLIENT_SECRET": "x", "GOOGLE_REDIRECT_URI": "http://localhost/x",
    "email_user": "test@example.invalid", "email_pass": "x",
    "SMTP_HOST": "127.0.0.1", "SMTP_PORT": str(_SMTP.server_address[1]),
    "KEYCLOAK_ISSUER": "", "KEYCLOAK_AUDIENCE": "", "KEYCLOAK_JWKS_URL": "",
    "INVIGORATE_AUTH_BASE_URL": _EXT, "INVIGORATE_ADMIN_API_BASE_URL": _EXT,
    "INVIGORATE_INTERNAL_API_KEY": "", "SOCKETIO_REDIS_URL": "",
    "FIREBASE_CREDENTIALS_PATH": "", "FIREBASE_CREDENTIALS_JSON": "", "BRAVO_SMS_API_URL": "", "BRAVO_API_KEY": "",
    "OPENAI_API_KEY": "", "PAYMENT_GATEWAY": "test", "ENABLE_DEV_TOKEN": "false",
    "UPLOAD_DIR": str(_TMP / "uploads"), "LOG_LEVEL": "WARNING", "DISABLE_BACKGROUND_JOBS": "true",
    "INTERNAL_API_KEY": "v2-internal-test-key",
    # Celery: in-memory broker, so queued tasks never wait on a Redis that is not there
    "CELERY_BROKER_URL": "memory://", "CELERY_RESULT_BACKEND": "cache+memory://",
    "CATALOG_SERVICE_URL": _EXT, "NOTIFICATIONS_SERVICE_URL": _EXT, "COMMUNICATION_SERVICE_URL": _EXT,
}
os.environ.update(COMMON)

# Test-only: PostgreSQL JSONB columns as JSON on SQLite.
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(_type, _compiler, **_kw):
    return "JSON"


def _sqlite(name):
    return "sqlite:///" + str(_TMP / name).replace("\\", "/")


# 1) the monolith (reference), on its own file - imported first so its settings bind to that file
os.environ["DATABASE_URL"] = _sqlite("monolith.db")
import app.main as _monolith  # noqa: E402,F401
from app.db.database import Base as _MonolithBase, engine as _monolith_engine  # noqa: E402

_MonolithBase.metadata.create_all(_monolith_engine)

# 2) this service, on its own file
os.environ["DATABASE_URL"] = _sqlite("service.db")
from notifications_service import model_registry  # noqa: E402,F401
from notifications_service.db.database import Base as _ServiceBase, engine as _service_engine  # noqa: E402

_ServiceBase.metadata.create_all(_service_engine)


import pytest  # noqa: E402

sys.path.insert(0, str(V2 / "tests" / "support"))


@pytest.fixture
def stubs(monkeypatch):
    import stubs as _stubs
    return _stubs.install(monkeypatch)
