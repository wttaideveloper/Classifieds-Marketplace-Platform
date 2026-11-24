"""Side-by-side compatibility run: monolith vs. gateway + services.

    python microservices/tests/compat/harness.py [--python PATH] [--report-dir DIR] [--roles ...]

Both stacks run as real processes on 127.0.0.1, each on its own isolated SQLite database and
upload directory, created empty from the unchanged models and given identical state:

* side A: the monolith, started exactly like ``run.py`` (``app.main:socket_app``);
* side B: the 11 services (each its own process, sharing database B, like production shares
  PostgreSQL in stage 1) behind the gateway.

Every operation of the monolith's OpenAPI document is called on both sides with the same
headers, tokens and generated bodies:

1. anonymous sweep: every operation without credentials (auth, validation, 404/405 behaviour);
2. stateful sweep per role (super_admin, admin, provider, customer): POSTs first (ids returned
   by each side are kept in that side's pool and reused in later paths and bodies), then GETs,
   then PUT/PATCH, then DELETEs;
3. Socket.IO: Engine.IO polling handshake and a WebSocket session through both front doors.

Compared per request: status code, selected headers, body (JSON after normalising generated
values: uuids are numbered in order of appearance, timestamps and JWTs are masked).
After each phase the two databases are compared table by table (rows normalised the same way).

External systems are unreachable by construction (SMTP, Invigorate auth, Firebase, SMS, OpenAI,
meeting providers point at nothing or are disabled; the payment gateway is the built-in test one).
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
MS_ROOT = HERE.parents[1]
REPO_ROOT = MS_ROOT.parent
SERVICES = [
    "identity", "catalog", "booking", "events", "learning", "commerce",
    "communication", "notifications", "content", "meetings", "moderation",
]
SECRET = "compat-secret-not-real"
HOST_HEADER = "api.compat.test"
FIXED_UUID = "0f0f0f0f-0000-4000-8000-00000000abcd"
USERS = {
    "super_admin": {"id": "550e8400-e29b-41d4-a716-446655440001", "role": "super_admin", "isSuperAdmin": True},
    "admin": {"id": "550e8400-e29b-41d4-a716-446655440000", "role": "admin"},
    "provider": {"id": "550e8400-e29b-41d4-a716-446655440020", "role": "provider"},
    "customer": {"id": "550e8400-e29b-41d4-a716-446655440030", "role": "customer"},
}
TENANT_ID = "7a7a7a7a-0000-4000-8000-000000000001"
COMPARED_HEADERS = ("content-type", "allow", "location", "www-authenticate", "content-disposition", "cache-control", "access-control-allow-origin", "access-control-allow-credentials")
PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
)


# ------------------------------------------------------------------ processes

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_STUBS: dict = {}


def external_stubs() -> dict:
    """Local stand-ins for external systems: an HTTP server answering 503 at once (Invigorate auth API)
    and a TCP server that closes at once (SMTP). Nothing leaves the machine and calls fail fast."""
    if not _STUBS:
        import socketserver
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Unavailable(BaseHTTPRequestHandler):
            def _answer(self):
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _answer

            def log_message(self, *args):
                pass

        class CloseAtOnce(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.close()

        http_srv = ThreadingHTTPServer(("127.0.0.1", 0), Unavailable)
        smtp_srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), CloseAtOnce)
        for srv in (http_srv, smtp_srv):
            threading.Thread(target=srv.serve_forever, daemon=True).start()
        _STUBS["http"] = f"http://127.0.0.1:{http_srv.server_address[1]}"
        _STUBS["smtp_port"] = str(smtp_srv.server_address[1])
    return _STUBS


def base_env(db_path: Path, upload_dir: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("DATABASE_", "KEYCLOAK", "INVIGORATE", "FIREBASE", "BRAVO", "SMTP", "OPENAI", "STRIPE", "RAZORPAY", "ZOOM", "GOOGLE", "MS_", "TEAMS"))}
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "ENVIRONMENT": "development",
        "DATABASE_URL": "sqlite:///" + str(db_path).replace("\\", "/"),
        "AUTO_CREATE_TABLES": "false",
        "SECRET_KEY": SECRET,
        "GOOGLE_CLIENT_ID": "x", "GOOGLE_CLIENT_SECRET": "x", "GOOGLE_REDIRECT_URI": "http://localhost/x",
        "email_user": "compat@example.invalid", "email_pass": "x",
        "SMTP_HOST": "127.0.0.1", "SMTP_PORT": external_stubs()["smtp_port"],
        "KEYCLOAK_ISSUER": "", "KEYCLOAK_AUDIENCE": "", "KEYCLOAK_JWKS_URL": "",
        "INVIGORATE_AUTH_BASE_URL": external_stubs()["http"], "INVIGORATE_ADMIN_API_BASE_URL": external_stubs()["http"],
        "CELERY_BROKER_URL": "memory://", "CELERY_RESULT_BACKEND": "cache+memory://",
        "INVIGORATE_INTERNAL_API_KEY": "",
        "SOCKETIO_REDIS_URL": "",
        "FIREBASE_CREDENTIALS_PATH": "", "FIREBASE_CREDENTIALS_JSON": "",
        "BRAVO_SMS_API_URL": "", "BRAVO_API_KEY": "", "OPENAI_API_KEY": "",
        "PAYMENT_GATEWAY": "test",
        "UPLOAD_DIR": str(upload_dir),
        "ENABLE_DEV_TOKEN": "true",
        "CORS_ORIGINS": "http://frontend.compat.test",
        "LOG_LEVEL": "WARNING",
    })
    return env


class Stack:
    def __init__(self, python: str, workdir: Path):
        self.python = python
        self.workdir = workdir
        self.procs: list[subprocess.Popen] = []
        self.logs: list[Path] = []

    def start(self, name: str, target: str, port: int, env: dict, pythonpath: list[str]) -> None:
        env = dict(env)
        env["PYTHONPATH"] = os.pathsep.join(pythonpath)
        log = self.workdir / f"{name}.log"
        fh = open(log, "wb")
        cwd = self.workdir / f"cwd-{name}"
        cwd.mkdir(exist_ok=True)  # empty cwd: the legacy settings must not find the developer's .env
        proc = subprocess.Popen([self.python, str(HERE / "launch.py"), target, str(port)], env=env, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT)
        self.procs.append(proc)
        self.logs.append(log)

    @staticmethod
    def wait_http(url: str, timeout: float = 120) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if httpx.get(url, timeout=2).status_code < 500:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        raise RuntimeError(f"not ready: {url}")

    def stop(self) -> None:
        for p in self.procs:
            p.terminate()
        for p in self.procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


def create_schema(python: str, db_path: Path) -> None:
    code = (
        "import sys; sys.path.insert(0, %r); sys.path.insert(0, %r)\n"
        "import launch  # JSONB shim\n"
        "from app.db.database import Base, engine\n"
        "import app.main\n"
        "Base.metadata.create_all(engine)\n"
    ) % (str(REPO_ROOT), str(HERE))
    env = base_env(db_path, db_path.parent / "unused-uploads")
    env["PYTHONPATH"] = str(REPO_ROOT)
    subprocess.run([python, "-c", code], env=env, cwd=db_path.parent, check=True, capture_output=True)


# ------------------------------------------------------------------ tokens

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_token(claims: dict) -> str:
    import hashlib
    import hmac

    header = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = dict(claims)
    payload.setdefault("exp", 4102444800)  # 2100-01-01, fixed so both sides see the same token
    payload.setdefault("tenant_id", TENANT_ID)
    payload.setdefault("email", f"{claims['role']}@example.invalid")
    payload.setdefault("name", f"Compat {claims['role']}")
    body = b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = hmac.new(SECRET.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
    return f"{header}.{body}.{b64url(sig)}"


# ------------------------------------------------------------------ normalisation

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
# random placeholder meeting links the monolith invents when no provider is connected (event_service._auto_meeting_link)
PLACEHOLDER_LINK_RE = re.compile(r"https://zoom[.]us/j/[0-9]{10}|https://meet[.]google[.]com/[a-z]{3}-[a-z]{4}-[a-z]{3}")
GEN_KEY_RE = re.compile(r"\b(section|field)_[0-9a-f]{12}\b")  # generated form-builder ids
HEX32_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-f]{32}(?![0-9a-fA-F])")
TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?")
JWT_RE = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+")
EPOCH_KEYS = {"exp", "iat", "nbf", "expires_at_epoch", "timestamp", "ts", "server_time"}
VOLATILE_KEYS = {"process_pid", "qr_code", "request_id", "duration_ms", "elapsed_ms", "uptime_seconds", "response_time_ms", "sid", "pingInterval"}


class Normalizer:
    """Numbers generated ids in order of first appearance (one numbering per side)."""

    def __init__(self, fixed: set[str]):
        self.fixed = {f.lower() for f in fixed}
        self.ids: dict[str, str] = {}

    def _id(self, value: str) -> str:
        v = value.lower()
        if v in self.fixed:
            return v
        if v not in self.ids:
            self.ids[v] = f"<id{len(self.ids) + 1}>"
        return self.ids[v]

    def text(self, s: str) -> str:
        s = JWT_RE.sub("<jwt>", s)
        s = PLACEHOLDER_LINK_RE.sub("<placeholder-meeting-link>", s)
        s = UUID_RE.sub(lambda m: self._id(m.group(0)), s)
        s = HEX32_RE.sub(lambda m: self._id(m.group(0)), s)
        s = GEN_KEY_RE.sub(lambda m: m.group(1) + "_" + self._id(m.group(0)), s)
        s = TS_RE.sub("<ts>", s)
        return s

    def value(self, v, key: str | None = None):
        if key in VOLATILE_KEYS:
            return "<volatile>"
        if isinstance(v, dict):
            return {k: self.value(x, k) for k, x in v.items()}
        if isinstance(v, list):
            return [self.value(x) for x in v]
        if isinstance(v, str):
            return self.text(v)
        if key in EPOCH_KEYS and isinstance(v, (int, float)):
            return "<epoch>"
        return v

    def peek_value(self, v):
        """Normalise without assigning new numbers (for database rows)."""
        saved = dict(self.ids)
        out = self.value(v)
        self.ids = saved
        return out


# ------------------------------------------------------------------ request generation

def resolve_ref(spec: dict, schema: dict) -> dict:
    seen = 0
    while isinstance(schema, dict) and "$ref" in schema and seen < 20:
        ref = schema["$ref"].split("/")[-1]
        schema = spec["components"]["schemas"].get(ref, {})
        seen += 1
    return schema or {}


def singular(word: str) -> str:
    word = word.replace("-", "_")
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("ses") or word.endswith("xes"):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def resource_key(path: str) -> str | None:
    static = [p for p in path.split("/") if p and not p.startswith("{") and p not in ("api", "v1")]
    return singular(static[-1]) + "_id" if static else None


class Pool:
    def __init__(self):
        self.ids: dict[str, str] = {}

    def remember(self, path: str, body) -> None:
        if not isinstance(body, dict):
            return
        candidate = body.get("id")
        if candidate is None and isinstance(body.get("data"), dict):
            candidate = body["data"].get("id")
        if isinstance(candidate, (str, int)) and UUID_RE.fullmatch(str(candidate)):
            key = resource_key(path)
            if key:
                self.ids.setdefault(key, str(candidate))
        for k, v in body.items():
            if k.endswith("_id") and isinstance(v, str) and UUID_RE.fullmatch(v):
                self.ids.setdefault(k, v)

    def get(self, name: str) -> str | None:
        return self.ids.get(name)


def sample_value(spec, schema, name: str, pool: Pool, depth: int = 0):
    schema = resolve_ref(spec, schema)
    if depth > 6:
        return None
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            options = [resolve_ref(spec, s) for s in schema[key]]
            options = [o for o in options if o.get("type") != "null"] or options
            if key == "allOf":
                merged = {}
                for o in options:
                    merged.update(o)
                return sample_value(spec, merged, name, pool, depth + 1)
            return sample_value(spec, options[0], name, pool, depth + 1)
    if "default" in schema and schema["default"] is not None:
        return schema["default"]
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    t = schema.get("type")
    fmt = schema.get("format", "")
    if t == "object" or "properties" in schema:
        out = {}
        required = set(schema.get("required", []))
        for prop, sub in (schema.get("properties") or {}).items():
            if prop in required:
                out[prop] = sample_value(spec, sub, prop, pool, depth + 1)
        return out
    if t == "array":
        if schema.get("minItems", 0) > 0:
            return [sample_value(spec, schema.get("items", {}), name, pool, depth + 1)]
        return []
    if t == "integer":
        return max(1, schema.get("minimum", 1) or 1)
    if t == "number":
        return float(max(1, schema.get("minimum", 1) or 1))
    if t == "boolean":
        return True
    if fmt == "uuid" or name.endswith("_id"):
        return pool.get(name) or FIXED_UUID
    if fmt == "date-time":
        return "2027-01-15T10:00:00Z"
    if fmt == "date":
        return "2027-01-15"
    if fmt == "time":
        return "10:00:00"
    if fmt == "email" or "email" in name:
        return "compat@example.com"
    if fmt in ("uri", "url") or name.endswith("url"):
        return "https://example.com/compat"
    if name in ("currency", "currency_code"):
        return "INR"
    if name in ("country", "country_code"):
        return "IN"
    min_len = schema.get("minLength", 0) or 0
    return ("Compat " + name)[: max(min_len, 1) + 40].ljust(min_len, "x")


def build_operations(spec: dict) -> list[dict]:
    ops = []
    for path, item in spec["paths"].items():
        for method, op in item.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            ops.append({"path": path, "method": method.upper(), "op": op})
    return ops


def materialize(spec, opdef, pool: Pool) -> dict:
    op = opdef["op"]
    path = opdef["path"]
    query = {}
    for param in op.get("parameters", []):
        name = param["name"]
        schema = param.get("schema", {})
        if param["in"] == "path":
            value = pool.get(name) or sample_value(spec, schema, name, pool)
            path = path.replace("{" + name + "}", str(value if value is not None else FIXED_UUID))
            path = re.sub(r"\{" + re.escape(name) + r":[^}]+\}", str(value), path)
        elif param["in"] == "query" and param.get("required"):
            query[name] = sample_value(spec, schema, name, pool)
    req = {"method": opdef["method"], "path": path, "params": query, "json": None, "files": None, "data": None}
    body = op.get("requestBody")
    if body:
        content = body.get("content", {})
        if "application/json" in content:
            req["json"] = sample_value(spec, content["application/json"].get("schema", {}), "body", pool)
        elif "multipart/form-data" in content or "application/x-www-form-urlencoded" in content:
            ctype = "multipart/form-data" if "multipart/form-data" in content else "application/x-www-form-urlencoded"
            schema = resolve_ref(spec, content[ctype].get("schema", {}))
            data, files = {}, {}
            for prop, sub in (schema.get("properties") or {}).items():
                sub = resolve_ref(spec, sub)
                is_file = sub.get("format") == "binary" or sub.get("contentMediaType") or (sub.get("type") == "array" and resolve_ref(spec, sub.get("items", {})).get("format") == "binary")
                if is_file:
                    files[prop] = ("compat.png", PNG_1x1, "image/png")
                elif prop in set(schema.get("required", [])):
                    v = sample_value(spec, sub, prop, pool)
                    data[prop] = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
            req["data"], req["files"] = data or None, files or None
    return req


# ------------------------------------------------------------------ comparison

class Side:
    def __init__(self, name: str, base_url: str, fixed_ids: set[str]):
        self.name = name
        self.client = httpx.Client(base_url=base_url, timeout=120, headers={"Host": HOST_HEADER}, follow_redirects=False)
        self.norm = Normalizer(fixed_ids)
        self.pool = Pool()

    def call(self, req: dict, headers: dict) -> httpx.Response:
        kwargs = {"params": req["params"] or None, "headers": headers}
        if req["files"]:
            kwargs["files"] = req["files"]
            kwargs["data"] = req["data"]
        elif req["data"]:
            kwargs["data"] = req["data"]
        elif req["json"] is not None:
            kwargs["json"] = req["json"]
        return self.client.request(req["method"], req["path"], **kwargs)


def snapshot_response(side: Side, r: httpx.Response) -> dict:
    headers = {}
    for h in COMPARED_HEADERS:
        if h in r.headers:
            headers[h] = side.norm.text(r.headers[h].replace(str(side.client.base_url).rstrip("/"), "<base>"))
    cookies = sorted(side.norm.text(re.sub(r"expires=[^;]+", "expires=<ts>", c, flags=re.I)) for c in r.headers.get_list("set-cookie"))
    try:
        body = side.norm.value(r.json())
    except Exception:  # noqa: BLE001
        ctype = r.headers.get("content-type", "")
        body = side.norm.text(r.text) if ("text" in ctype or "json" in ctype or "html" in ctype) else f"<{len(r.content)} bytes>"
    return {"status": r.status_code, "headers": headers, "cookies": cookies, "body": body}


def db_snapshot(db_path: Path, norm: Normalizer) -> dict:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    out = {}
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for t in tables:
            rows = []
            for row in con.execute(f'SELECT * FROM "{t}"'):
                d = {}
                for k in row.keys():
                    v = row[k]
                    if isinstance(v, bytes):
                        v = f"<{len(v)} bytes>"
                    if isinstance(v, str):
                        try:
                            parsed = json.loads(v)
                            if isinstance(parsed, (dict, list)):
                                v = parsed
                        except ValueError:
                            pass
                    d[k] = v
                rows.append(json.dumps(norm.peek_value(d), sort_keys=True, default=str))
            out[t] = sorted(rows)
    finally:
        con.close()
    return out


def generic_ids(rows: list[str]) -> list[str]:
    return sorted(re.sub(r"<id\d+>|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32}", "<id>", r) for r in rows)


def compare_dbs(a: dict, b: dict) -> list[dict]:
    diffs = []
    for table in sorted(set(a) | set(b)):
        ra, rb = a.get(table, []), b.get(table, [])
        if ra == rb:
            continue
        if generic_ids(ra) == generic_ids(rb):
            continue  # same rows; only ids that never appeared in a response are numbered differently
        diffs.append({
            "table": table, "rows_monolith": len(ra), "rows_gateway": len(rb),
            "only_monolith": [x for x in ra if x not in rb][:3], "only_gateway": [x for x in rb if x not in ra][:3],
        })
    return diffs


# ------------------------------------------------------------------ main run

def run(args) -> dict:
    python = args.python
    work = Path(tempfile.mkdtemp(prefix="compat-run-"))
    db_a, db_b = work / "monolith.db", work / "services.db"
    up_a, up_b = work / "uploads-monolith", work / "uploads-services"
    up_a.mkdir(), up_b.mkdir()
    create_schema(python, db_a)
    shutil.copy(db_a, db_b)  # identical starting state

    stack = Stack(python, work)
    report: dict = {"workdir": str(work), "phases": [], "requests": 0, "differences": [], "db_differences": []}
    try:
        mono_port = free_port()
        stack.start("monolith", "app.main:socket_app", mono_port, base_env(db_a, up_a), [str(REPO_ROOT)])
        svc_ports = {}
        env_b = base_env(db_b, up_b)
        for svc in SERVICES:
            svc_ports[svc] = free_port()
            stack.start(svc, f"{svc}_service.main:app", svc_ports[svc], env_b,
                        [str(MS_ROOT / "services" / svc), str(MS_ROOT / "libs" / "service_common"), str(REPO_ROOT)])
        gw_port = free_port()
        gw_env = dict(env_b)
        for svc, port in svc_ports.items():
            gw_env[f"{svc.upper()}_SERVICE_URL"] = f"http://127.0.0.1:{port}"
        gw_env["GATEWAY_ECHO_REQUEST_ID"] = "false"  # compare headers exactly as the monolith sends them
        stack.start("gateway", "gateway_app.main:app", gw_port, gw_env, [str(MS_ROOT / "gateway"), str(MS_ROOT / "libs" / "service_common")])

        Stack.wait_http(f"http://127.0.0.1:{mono_port}/health")
        for svc, port in svc_ports.items():
            Stack.wait_http(f"http://127.0.0.1:{port}/_service/health")
        Stack.wait_http(f"http://127.0.0.1:{gw_port}/_gateway/health")

        fixed = {u["id"] for u in USERS.values()} | {FIXED_UUID, TENANT_ID}
        A = Side("monolith", f"http://127.0.0.1:{mono_port}", fixed)
        B = Side("gateway", f"http://127.0.0.1:{gw_port}", fixed)

        spec_a = A.client.get("/openapi.json").json()
        spec_b = B.client.get("/openapi.json").json()
        report["openapi_identical"] = spec_a == spec_b
        ops = build_operations(spec_a)
        report["operations"] = len(ops)
        routes = json.loads((MS_ROOT / "gateway" / "gateway_app" / "routes.json").read_text(encoding="utf-8"))["routes"]
        from starlette.routing import compile_path  # noqa: PLC0415
        compiled = [(compile_path(r["path"])[0], r["service"]) for r in routes]

        def owner(path_template: str) -> str:
            for rx, svc in compiled:
                if rx.match(path_template.split("?")[0]):
                    return svc
            return "?"

        per_service = defaultdict(Counter)

        def exchange(phase: str, req: dict, headers: dict, opdef: dict) -> None:
            ra = A.call(req["a"], headers)
            rb = B.call(req["b"], headers)
            report["requests"] += 1
            if 200 <= ra.status_code < 300 and req["a"]["method"] in ("POST", "PUT", "PATCH"):
                try:
                    A.pool.remember(opdef["path"], ra.json())
                except ValueError:
                    pass
            if 200 <= rb.status_code < 300 and req["b"]["method"] in ("POST", "PUT", "PATCH"):
                try:
                    B.pool.remember(opdef["path"], rb.json())
                except ValueError:
                    pass
            sa, sb = snapshot_response(A, ra), snapshot_response(B, rb)
            svc = owner(req["a"]["path"])
            bucket = "match" if sa == sb else "diff"
            per_service[svc][bucket] += 1
            per_service[svc][f"status_{ra.status_code // 100}xx"] += 1
            if sa != sb:
                report["differences"].append({
                    "phase": phase, "method": req["a"]["method"], "path": opdef["path"], "service": svc,
                    "monolith": _short(sa), "gateway": _short(sb),
                })

        # 1) anonymous sweep (no state change expected beyond public endpoints)
        for opdef in ops:
            req = {"a": materialize(spec_a, opdef, A.pool), "b": materialize(spec_a, opdef, B.pool)}
            exchange("anonymous", req, {"Origin": "http://frontend.compat.test"}, opdef)
        report["phases"].append("anonymous")
        report["db_differences"] += [dict(d, phase="anonymous") for d in compare_dbs(db_snapshot(db_a, A.norm), db_snapshot(db_b, B.norm))]

        # 2) stateful sweep per role
        order = {"POST": 0, "GET": 1, "PUT": 2, "PATCH": 2, "DELETE": 3}
        for role in args.roles:
            token = make_token(USERS[role])
            headers = {"Authorization": f"Bearer {token}"}
            for opdef in sorted(ops, key=lambda o: order[o["method"]]):
                req = {"a": materialize(spec_a, opdef, A.pool), "b": materialize(spec_a, opdef, B.pool)}
                exchange(role, req, headers, opdef)
            report["phases"].append(role)
            report["db_differences"] += [dict(d, phase=role) for d in compare_dbs(db_snapshot(db_a, A.norm), db_snapshot(db_b, B.norm))]

        # 3) malformed / wrong-scope credentials on a protected endpoint
        for label, hdrs in {
            "garbage-token": {"Authorization": "Bearer not-a-jwt"},
            "wrong-secret": {"Authorization": "Bearer " + make_token(USERS["admin"])[:-4] + "AAAA"},
            "chat-scoped-elsewhere": {"Authorization": "Bearer " + make_token({**USERS["customer"], "scope": "chat", "token_type": "chat"})},
            "cookie-session": {"Cookie": "access_token=" + make_token(USERS["provider"])},
        }.items():
            for path in ("/api/v1/auth/me", "/api/v1/conversations/provider", "/api/v1/cart/", "/api/v1/notifications/unread-count"):
                opdef = {"path": path, "method": "GET", "op": {}}
                req = {"a": {"method": "GET", "path": path, "params": {}, "json": None, "files": None, "data": None}}
                req["b"] = req["a"]
                exchange(f"auth:{label}", req, hdrs, opdef)

        # 4) CORS preflight
        for path in ("/api/v1/products/", "/api/v1/cart/checkout", "/api/v1/nope"):
            opdef = {"path": path, "method": "OPTIONS", "op": {}}
            req = {"a": {"method": "OPTIONS", "path": path, "params": {}, "json": None, "files": None, "data": None}}
            req["b"] = req["a"]
            exchange("cors", req, {"Origin": "http://frontend.compat.test", "Access-Control-Request-Method": "POST"}, opdef)

        # 5) Socket.IO
        report["socketio"] = socketio_checks(A, B, make_token(USERS["customer"]))

        report["per_service"] = {k: dict(v) for k, v in sorted(per_service.items())}
        report["background_logs"] = {p.name: _tail_errors(p) for p in stack.logs}
    finally:
        stack.stop()
    return report


def socketio_checks(A: Side, B: Side, token: str) -> dict:
    out = {}
    pa = A.client.get("/socket.io/", params={"EIO": "4", "transport": "polling"})
    pb = B.client.get("/socket.io/", params={"EIO": "4", "transport": "polling"})

    def open_packet(r):
        text = r.text
        if text.startswith("0"):
            data = json.loads(text[1:])
            data.pop("sid", None)
            return r.status_code, data
        return r.status_code, text[:80]

    out["polling_handshake_monolith"] = open_packet(pa)
    out["polling_handshake_gateway"] = open_packet(pb)
    out["polling_handshake_identical"] = open_packet(pa) == open_packet(pb)

    import asyncio

    async def ws_session(base: str):
        from websockets.asyncio.client import connect

        url = base.replace("http://", "ws://") + "/socket.io/?EIO=4&transport=websocket"
        frames = []
        async with connect(url, additional_headers={"Host": HOST_HEADER}, open_timeout=10) as ws:
            frames.append((await asyncio.wait_for(ws.recv(), 10))[:1])  # "0" open packet
            await ws.send("40" + json.dumps({"token": token}))
            reply = await asyncio.wait_for(ws.recv(), 10)
            frames.append(reply[:2])
            if reply.startswith("44"):
                frames.append(json.loads(reply[2:]))
        return frames

    try:
        wa = asyncio.run(ws_session(str(A.client.base_url)))
        wb = asyncio.run(ws_session(str(B.client.base_url)))
        out["websocket_monolith"], out["websocket_gateway"] = wa, wb
        out["websocket_identical"] = wa == wb
    except Exception as exc:  # noqa: BLE001
        out["websocket_error"] = repr(exc)
        out["websocket_identical"] = False
    return out


def _short(snapshot: dict) -> dict:
    s = copy.deepcopy(snapshot)
    text = json.dumps(s["body"], default=str)
    if len(text) > 600:
        s["body"] = text[:600] + "..."
    return s


def _tail_errors(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [l for l in lines if "Traceback" in l or " ERROR " in l][:20]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--report-dir", default=str(HERE / "reports"))
    parser.add_argument("--roles", nargs="*", default=["super_admin", "admin", "provider", "customer"])
    args = parser.parse_args(argv)
    started = time.time()
    report = run(args)
    report["seconds"] = round(time.time() - started, 1)
    out_dir = Path(args.report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "compat_report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(json.dumps({
        "requests": report["requests"], "differences": len(report["differences"]),
        "db_differences": len(report["db_differences"]), "openapi_identical": report.get("openapi_identical"),
        "socketio": {k: v for k, v in report.get("socketio", {}).items() if k.endswith("identical") or k.endswith("error")},
        "seconds": report["seconds"],
    }, indent=1))
    return 0 if not report["differences"] and not report["db_differences"] else 1


if __name__ == "__main__":
    sys.exit(main())
