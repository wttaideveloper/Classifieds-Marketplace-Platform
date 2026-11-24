"""Hand-written flows run on the monolith and on a v2 service side by side (test-only).

A step: {"as": role|None, "method": "POST", "path": "/api/v1/x/{ent}", "json": {...}, "save": {"ent": "id"}}
``{name}`` placeholders in path/json/params are filled from values each side saved itself, so ids
created by each side are used on that side. Responses are compared after normalisation.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare_base as cb  # noqa: E402


def _fill(value, saved):
    if isinstance(value, str):
        m = re.fullmatch(r"\{(\w+)\}", value)
        if m and m.group(1) in saved:
            return saved[m.group(1)]
        return re.sub(r"\{(\w+)\}", lambda mm: str(saved.get(mm.group(1), mm.group(0))), value)
    if isinstance(value, dict):
        return {k: _fill(v, saved) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, saved) for v in value]
    return value


def _get(body, dotted):
    for part in dotted.split("."):
        if isinstance(body, list):
            body = body[int(part)]
        else:
            body = (body or {}).get(part)
    return body


def run_flow(monolith_client, service_client, steps, *, extra_headers=None):
    cb.SECRET = os.environ["SECRET_KEY"]
    fixed = {u["id"] for u in cb.USERS.values()} | {cb.FIXED_UUID, cb.TENANT_ID}
    sides = [
        {"client": monolith_client, "saved": {}, "norm": cb.Normalizer(fixed)},
        {"client": service_client, "saved": {}, "norm": cb.Normalizer(fixed)},
    ]
    diffs, log = [], []
    for n, step in enumerate(steps):
        results = []
        for side in sides:
            headers = dict(extra_headers or {})
            if step.get("as"):
                headers["Authorization"] = "Bearer " + cb.make_token(cb.USERS[step["as"]])
            client = side["client"]
            r = client.request(
                step["method"], _fill(step["path"], side["saved"]),
                json=_fill(step.get("json"), side["saved"]) if step.get("json") is not None else None,
                params=_fill(step.get("params"), side["saved"]),
                headers=headers,
            )
            try:
                body = r.json()
            except ValueError:
                body = r.text
            for name, dotted in (step.get("save") or {}).items():
                value = _get(body, dotted) if 200 <= r.status_code < 300 else None
                if value is not None:
                    side["saved"][name] = value
            norm = side["norm"].value(body)
            results.append((r.status_code, norm))
        log.append((n, step["method"], step["path"], results[0][0], results[1][0]))
        if results[0] != results[1]:
            diffs.append({"step": n, "method": step["method"], "path": step["path"],
                          "monolith": [results[0][0], json.dumps(results[0][1], default=str)[:500]],
                          "service": [results[1][0], json.dumps(results[1][1], default=str)[:500]]})
        if step.get("expect") and results[0][0] != step["expect"]:
            diffs.append({"step": n, "path": step["path"], "unexpected_monolith_status": results[0][0], "expected": step["expect"],
                          "body": json.dumps(results[0][1], default=str)[:300]})
    return diffs, log, sides
