"""Gateway tests: routing precedence (on the real, generated route table) and proxy mechanics
(headers, streaming, error mapping, timeouts, retry policy). Upstream services are mocked."""
import json
import sys
from pathlib import Path

import httpx
import pytest

GATEWAY_DIR = Path(__file__).resolve().parents[1]
MS_ROOT = GATEWAY_DIR.parent
for p in (str(GATEWAY_DIR), str(MS_ROOT / "kit")):
    if p not in sys.path:
        sys.path.insert(0, p)

from starlette.testclient import TestClient  # noqa: E402

from gateway_app.config import SERVICE_NAMES, GatewaySettings  # noqa: E402
from gateway_app.main import create_app  # noqa: E402
from gateway_app.routing import RouteTable, normalize_socketio_path  # noqa: E402
from mskit.http_client import CallPolicy  # noqa: E402

UUID = "550e8400-e29b-41d4-a716-446655440000"


@pytest.fixture(scope="module")
def table():
    return RouteTable.load()


def settings(**policy):
    return GatewaySettings(
        service_urls={n: f"http://{n}.internal:8000" for n in SERVICE_NAMES},
        policy=CallPolicy(retry_backoff=0.0, **policy),
    )


class Recorder:
    """Mock upstream: records requests per service and answers with ``reply(request)``."""

    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply or (lambda req: httpx.Response(200, json={"ok": True}))

    def transports(self):
        def make(name):
            def handler(request):
                self.calls.append((name, request))
                return self.reply(request)
            return httpx.MockTransport(handler)
        return {n: make(n) for n in SERVICE_NAMES}


# --- routing precedence (real table) -------------------------------------------------------

@pytest.mark.parametrize("method,path,service,kind", [
    ("GET", f"/api/v1/events/media/{UUID}", "events", "full"),
    ("GET", f"/api/v1/events/{UUID}", "events", "full"),
    ("GET", f"/api/v1/enterprises/{UUID}", "catalog", "full"),
    ("GET", "/api/v1/products/", "catalog", "full"),
    ("GET", "/api/v1/products", "catalog", "redirect_slash"),
    ("GET", f"/api/v1/admin/events/{UUID}/approve", "events", "partial"),
    ("POST", f"/api/v1/admin/events/{UUID}/approve", "events", "full"),
    ("GET", "/api/v1/notifications/unread-count", "communication", "full"),
    ("GET", "/api/v1/courses/", "learning", "full"),
    ("GET", "/api/v1/me/trainings", "learning", "full"),
    ("GET", "/api/v1/auth/integration", "identity", "full"),
    ("GET", "/api/v1/cart/", "commerce", "full"),
    ("GET", "/health", "identity", "full"),
    ("GET", "/openapi.json", "identity", "full"),
    ("GET", "/api/v1/this-does-not-exist", "identity", "default"),
])
def test_resolution_examples(table, method, path, service, kind):
    decision = table.resolve(path, method)
    assert (decision.service, decision.kind) == (service, kind)


def test_every_route_resolves_to_its_own_service_or_an_earlier_shadowing_route(table):
    """Fill each route template with sample values; the decision must be that route or an earlier one
    (an earlier route that also matches shadows it in the monolith too)."""
    samples = {"uuid": UUID, "int": "1", "path": "a/b.txt", "str": "sample", "float": "1.5"}
    from starlette.routing import compile_path
    for entry in table.entries:
        _regex, fmt, convertors = compile_path(entry.path)
        values = {}
        for name, conv in convertors.items():
            kind = type(conv).__name__.replace("Convertor", "").lower()
            values[name] = samples.get(kind, "sample")
        concrete = fmt.format(**values)
        method = sorted(entry.methods)[0] if entry.methods else "GET"
        decision = table.resolve(concrete, method)
        first = next(e for e in table.entries if table._path_matches(e, concrete) and (not e.methods or method in e.methods))
        assert first.index <= entry.index
        assert decision.service == first.service


def test_slash_redirect_goes_to_the_route_owner(table):
    decision = table.resolve("/api/v1/products", "GET")
    assert decision.kind == "redirect_slash" and decision.service == "catalog"


def test_socketio_prefix(table):
    assert table.resolve("/socket.io/", "GET").service == "communication"
    assert table.resolve("/socket.io/", "GET", "websocket").kind == "socketio"
    assert normalize_socketio_path("/socket.io", environment="production") == "/api/socket.io"
    assert normalize_socketio_path("/socket.io", environment="production", standalone=True) == "/socket.io"
    assert normalize_socketio_path("custom/") == "/custom"


def test_route_table_covers_all_services(table):
    assert {e.service for e in table.entries} == set(SERVICE_NAMES)


# --- proxy mechanics -------------------------------------------------------------------------

def test_forwards_path_query_body_and_headers():
    rec = Recorder()
    client = TestClient(create_app(settings(), transports=rec.transports()), base_url="https://api.example.test")
    r = client.post(
        "/api/v1/cart/checkout?x=1&y=%2F",
        content=b'{"a": 1}',
        headers={"Authorization": "Bearer t", "Cookie": "access_token=c", "Content-Type": "application/json"},
    )
    assert r.status_code == 200 and r.json() == {"ok": True}
    name, req = rec.calls[0]
    assert name == "commerce"
    assert req.url.raw_path == b"/api/v1/cart/checkout?x=1&y=%2F"
    assert req.content == b'{"a": 1}'
    assert req.headers["host"] == "api.example.test"
    assert req.headers["authorization"] == "Bearer t"
    assert req.headers["cookie"] == "access_token=c"
    assert req.headers["x-forwarded-proto"] == "https"
    assert req.headers["x-forwarded-host"] == "api.example.test"
    assert req.headers["x-request-id"] == r.headers["x-request-id"]


def test_client_request_id_is_kept_and_echoed():
    rec = Recorder()
    client = TestClient(create_app(settings(), transports=rec.transports()))
    r = client.get("/api/v1/products", headers={"X-Request-ID": "abc-123"})
    assert rec.calls[0][1].headers["x-request-id"] == "abc-123"
    assert r.headers["x-request-id"] == "abc-123"


def test_response_status_headers_and_cookies_pass_through():
    def reply(_req):
        return httpx.Response(
            422,
            headers=[("content-type", "application/json"), ("set-cookie", "a=1; Path=/"), ("set-cookie", "b=2; Path=/"), ("x-custom", "v")],
            content=json.dumps({"detail": [{"loc": ["body"], "msg": "Field required", "type": "missing"}]}).encode(),
        )
    rec = Recorder(reply)
    client = TestClient(create_app(settings(), transports=rec.transports()))
    r = client.post("/api/v1/products", json={})
    assert r.status_code == 422
    assert r.json()["detail"][0]["msg"] == "Field required"
    assert r.headers.get_list("set-cookie") == ["a=1; Path=/", "b=2; Path=/"]
    assert r.headers["x-custom"] == "v"


def test_large_upload_is_streamed_unchanged():
    payload = bytes(range(256)) * 20000  # ~5 MB
    rec = Recorder(lambda req: httpx.Response(201, content=str(len(req.read())).encode()))
    client = TestClient(create_app(settings(), transports=rec.transports()))
    r = client.post("/api/v1/uploads", content=payload, headers={"content-type": "application/octet-stream"})
    assert r.status_code == 201 and r.text == str(len(payload))
    assert rec.calls[0][0] == "content"


def test_connect_error_on_get_is_retried_then_502():
    attempts = []

    def reply(req):
        attempts.append(req)
        raise httpx.ConnectError("refused", request=req)
    rec = Recorder(reply)
    client = TestClient(create_app(settings(retry_attempts=2), transports=rec.transports()))
    r = client.get("/api/v1/products")
    assert r.status_code == 502 and r.json() == {"detail": "Upstream service unavailable"}
    assert len(attempts) == 3


def test_post_is_never_retried():
    attempts = []

    def reply(req):
        attempts.append(req)
        raise httpx.ConnectError("refused", request=req)
    rec = Recorder(reply)
    client = TestClient(create_app(settings(retry_attempts=2), transports=rec.transports()))
    r = client.post("/api/v1/payments/webhook/test", content=b"{}")
    assert r.status_code == 502
    assert len(attempts) == 1


def test_get_recovers_after_a_refused_connection():
    attempts = []

    def reply(req):
        attempts.append(req)
        if len(attempts) == 1:
            raise httpx.ConnectError("refused", request=req)
        return httpx.Response(200, json={"ok": True})
    rec = Recorder(reply)
    client = TestClient(create_app(settings(retry_attempts=2), transports=rec.transports()))
    assert client.get("/api/v1/products").status_code == 200
    assert len(attempts) == 2


def test_read_timeout_is_504_and_not_retried():
    attempts = []

    def reply(req):
        attempts.append(req)
        raise httpx.ReadTimeout("slow", request=req)
    rec = Recorder(reply)
    client = TestClient(create_app(settings(retry_attempts=2), transports=rec.transports()))
    r = client.get("/api/v1/products")
    assert r.status_code == 504 and r.json() == {"detail": "Upstream service timed out"}
    assert len(attempts) == 1


def test_internal_paths_are_not_exposed():
    rec = Recorder()
    client = TestClient(create_app(settings(), transports=rec.transports()))
    assert client.get("/_service/ready").status_code == 404
    assert rec.calls == []
    r = client.get("/_gateway/health")
    assert r.json() == {"status": "ok", "service": "gateway"}


def test_gateway_ready_aggregates_services():
    rec = Recorder(lambda req: httpx.Response(200 if "catalog" not in str(req.url) else 503, json={}))
    client = TestClient(create_app(settings(), transports=rec.transports()))
    r = client.get("/_gateway/ready")
    assert r.status_code == 503
    assert r.json()["services"]["catalog"] == "status 503"
    assert r.json()["services"]["identity"] == "ok"
