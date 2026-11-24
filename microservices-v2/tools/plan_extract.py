"""Plan a service extraction: which monolith modules it must own, and where it reaches into other domains.

    python microservices-v2/tools/plan_extract.py content            # print the plan
    python microservices-v2/tools/plan_extract.py content --write    # also (re)write services/content/extract.json

Starting from the service's endpoint modules (tools/ownership.json), follows imports:
* modules of the same domain, and domain-free shared utilities, are owned (copied into the service);
* shared core already provided by mskit (settings, auth, errors, https links) is not copied;
* modules of another domain are NOT followed: each such edge is a cross-service dependency that
  must become an API call or an event. They are listed with the importing line.
"""
from __future__ import annotations

import argparse
import ast
import collections
import json
import sys
from pathlib import Path

V2 = Path(__file__).resolve().parents[1]
REPO = V2.parent
APP = REPO / "app"
sys.path.insert(0, str(V2 / "tools"))
from domains import domain_of_module  # noqa: E402

PROVIDED_BY_KIT = {
    "app.core.config", "app.core.dependencies", "app.core.security", "app.core.token_auth", "app.core.auth_context",
    "app.services.invigorate_auth_client", "app.services.super_admin_identity", "app.exceptions",
    "app.exceptions.custom_exception", "app.exceptions.problem", "app.core.https_links", "app.utils.public_urls",
    "app.db.database", "app.db",
}


def all_modules() -> dict[str, Path]:
    mods = {}
    for p in APP.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        m = ".".join(p.relative_to(REPO).with_suffix("").parts)
        if m.endswith(".__init__"):
            m = m[: -len(".__init__")]
        mods[m] = p
    return mods


def imports_of(path: Path, mods: dict) -> list[tuple[str, int]]:
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app"):
            found = False
            for a in node.names:
                full = f"{node.module}.{a.name}"
                if full in mods:
                    out.append((full, node.lineno))
                    found = True
            if not found:
                out.append((node.module, node.lineno))
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("app."):
                    out.append((a.name, node.lineno))
    return out


def plan(service: str):
    own = json.loads((V2 / "tools" / "ownership.json").read_text(encoding="utf-8"))
    mods = all_modules()
    provided = PROVIDED_BY_KIT | ({"app.core.catalog_access"} if service != "catalog" else set())
    starts = sorted({f"app.api.v1.endpoints.{m['module']}" for m in own["mounts"] if m["service"] == service})
    owned: set[str] = set()
    edges: dict[str, list[str]] = collections.defaultdict(list)
    stack = list(starts)
    while stack:
        m = stack.pop()
        if m in owned or m not in mods or m in provided:
            continue
        owned.add(m)
        for target, line in imports_of(mods[m], mods):
            if target in provided or target not in mods:
                continue
            dom = domain_of_module(target)
            if dom in (service, None) or target in starts:
                stack.append(target)
            else:
                edges[dom].append(f"{mods[m].relative_to(APP).as_posix()}:{line} -> {target}")
    return starts, owned, edges, mods


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("service")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    starts, owned, edges, mods = plan(a.service)
    files = sorted(mods[m].relative_to(APP).as_posix() for m in owned)
    loc = sum(len(mods[m].read_text(encoding="utf-8").splitlines()) for m in owned)
    print(f"{a.service}: {len(files)} modules, {loc} lines owned")
    for dom, items in sorted(edges.items(), key=lambda kv: -len(kv[1])):
        print(f"  -> {dom}: {len(items)} import edges")
        for i in items[:40]:
            print("       " + i)
    if a.write:
        from domains import TABLES_BY_SERVICE
        path = V2 / "services" / a.service / "extract.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"service": a.service, "tables": TABLES_BY_SERVICE[a.service], "files": files}, indent=1) + "\n", encoding="utf-8")
        print("written", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
