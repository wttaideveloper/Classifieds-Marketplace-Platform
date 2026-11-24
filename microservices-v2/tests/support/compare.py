"""In-process behavioural comparison: monolith vs. one v2 service (test-only).

For every operation of the monolith's OpenAPI document that the gateway sends to the service:
anonymous sweep, then per role POST -> GET -> PUT/PATCH -> DELETE with ids each side created itself.
Compared: status, key headers, normalised body; then the rows of the service's own tables.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import compare_base as cb  # noqa: E402

V2 = HERE.parents[1]


class _Side:
    def __init__(self, client, fixed):
        self.client = client
        self.norm = cb.Normalizer(fixed)
        self.pool = cb.Pool()

    call = cb.Side.call


def _sqlite_path(engine) -> Path:
    return Path(engine.url.database)


def compare_service(service: str, monolith_client, service_client, monolith_engine, service_engine, owned_tables,
                    roles=("super_admin", "admin", "provider", "customer"), skip=()):
    cb.SECRET = os.environ["SECRET_KEY"]
    routes = json.loads((V2 / "gateway" / "gateway_app" / "routes.json").read_text(encoding="utf-8"))["routes"]
    from starlette.routing import compile_path

    compiled = [(compile_path(r["path"])[0], r["service"]) for r in routes]

    def owner(path):
        for rx, svc in compiled:
            if rx.match(path):
                return svc
        return None

    spec = monolith_client.get("/openapi.json").json()
    ops = [o for o in cb.build_operations(spec) if owner(o["path"]) == service and (o["method"], o["path"]) not in skip]
    fixed = {u["id"] for u in cb.USERS.values()} | {cb.FIXED_UUID, cb.TENANT_ID}
    A, B = _Side(monolith_client, fixed), _Side(service_client, fixed)
    diffs, stats, slow = [], Counter(), []

    def exchange(phase, opdef, headers):
        ra_req, rb_req = cb.materialize(spec, opdef, A.pool), cb.materialize(spec, opdef, B.pool)
        import time
        t0 = time.perf_counter()
        ra = A.call(ra_req, headers)
        t1 = time.perf_counter()
        rb = B.call(rb_req, headers)
        t2 = time.perf_counter()
        if t1 - t0 > 1 or t2 - t1 > 1:
            slow.append((phase, opdef["method"], opdef["path"], round(t1 - t0, 1), round(t2 - t1, 1)))
        for side, r, req in ((A, ra, ra_req), (B, rb, rb_req)):
            if 200 <= r.status_code < 300 and req["method"] in ("POST", "PUT", "PATCH"):
                try:
                    side.pool.remember(opdef["path"], r.json())
                except ValueError:
                    pass
        sa, sb = cb.snapshot_response(A, ra), cb.snapshot_response(B, rb)
        stats[f"{ra.status_code // 100}xx"] += 1
        if sa != sb:
            diffs.append({"phase": phase, "method": opdef["method"], "path": opdef["path"], "monolith": cb._short(sa), "service": cb._short(sb)})

    for opdef in ops:
        exchange("anonymous", opdef, {})
    order = {"POST": 0, "GET": 1, "PUT": 2, "PATCH": 2, "DELETE": 3}
    for role in roles:
        headers = {"Authorization": "Bearer " + cb.make_token(cb.USERS[role])}
        for opdef in sorted(ops, key=lambda o: order[o["method"]]):
            exchange(role, opdef, headers)

    snap_a = {t: v for t, v in cb.db_snapshot(_sqlite_path(monolith_engine), A.norm).items() if t in owned_tables}
    snap_b = {t: v for t, v in cb.db_snapshot(_sqlite_path(service_engine), B.norm).items() if t in owned_tables}
    db_diffs = cb.compare_dbs(snap_a, snap_b)
    rows = {t: len(v) for t, v in snap_a.items()}
    return {"operations": len(ops), "requests": sum(stats.values()), "status_classes": dict(stats), "differences": diffs, "db_differences": db_diffs, "rows_monolith": rows, "slow_requests": slow}
