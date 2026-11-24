"""Copy a service's own modules out of the monolith and rewrite their imports.

    python microservices-v2/tools/extract.py identity [--force]

* Reads ``services/<name>/extract.json`` (the modules this service owns, monolith paths).
* Copies each into ``services/<name>/<name>_service/`` keeping the monolith layout
  (``api/v1/endpoints``, ``services``, ``repository``, ``schemas``, ``models``...).
* Rewrites imports: shared core -> ``mskit``; the database module -> the service's own
  ``db/database.py``; owned modules -> ``<name>_service``.
* Lists every import that still points at the monolith (``app.``): those are dependencies on
  other services and must be replaced by an API client or an event before the service is done.

The monolith is only read. Files that exist already are not overwritten without ``--force``
(they may carry hand-made cross-service changes).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

V2 = Path(__file__).resolve().parents[1]
REPO = V2.parent
APP = REPO / "app"

SHARED = {
    "app.core.config": "mskit.settings",
    "app.core.dependencies": "mskit.auth.dependencies",
    "app.core.security": "mskit.auth.security",
    "app.core.token_auth": "mskit.auth.token_auth",
    "app.core.auth_context": "mskit.auth.auth_context",
    "app.services.invigorate_auth_client": "mskit.auth.invigorate_auth_client",
    "app.services.super_admin_identity": "mskit.auth.super_admin_identity",
    "app.exceptions.custom_exception": "mskit.custom_exception",
    "app.exceptions.problem": "mskit.problem",
    "app.exceptions": "mskit",
    "app.core.https_links": "mskit.https_links",
    "app.utils.public_urls": "mskit.public_urls",
}
# "from app.services import invigorate_auth_client" style
SHARED_FROM_PACKAGE = {
    ("app.services", "invigorate_auth_client"): "mskit.auth",
    ("app.services", "super_admin_identity"): "mskit.auth",
    ("app.exceptions", "problem"): "mskit",
    ("app.exceptions", "custom_exception"): "mskit",
}


def module_name(path: str) -> str:
    return path[:-3].replace("/", ".") if path.endswith(".py") else path.replace("/", ".")


def rewrite(source: str, owned: set[str], pkg: str) -> str:
    if pkg != "catalog_service":
        source = source.replace("from app.core.catalog_access import", "from mskit.auth.access import")
    def repl_module(mod: str) -> str:
        if mod in SHARED:
            return SHARED[mod]
        if mod == "app.db.database":
            return f"{pkg}.db.database"
        if mod in owned or any(o.startswith(mod + ".") for o in owned):
            return pkg + mod[len("app"):]
        return mod

    def from_import(m: re.Match) -> str:
        indent, mod, names = m.group(1), m.group(2), m.group(3)
        first = names.strip().split(",")[0].strip().split(" ")[0].strip("()")
        key = (mod, first)
        if key in SHARED_FROM_PACKAGE and "," not in names:
            return f"{indent}from {SHARED_FROM_PACKAGE[key]} import {names}"
        # "from app.repository import chat_repo": judge by the imported module, not the package
        submodule = f"{mod}.{first}"
        if (APP.parent / (submodule.replace(".", "/") + ".py")).exists() and submodule not in owned and submodule not in SHARED:
            return m.group(0)
        return f"{indent}from {repl_module(mod)} import {names}"

    source = re.sub(r"^(\s*)from (app(?:\.[A-Za-z_0-9]+)*) import (.+)$", from_import, source, flags=re.M)
    source = re.sub(r"^(\s*)import (app(?:\.[A-Za-z_0-9]+)+)", lambda m: f"{m.group(1)}import {repl_module(m.group(2))}", source, flags=re.M)
    return source


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("service")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    svc_dir = V2 / "services" / args.service
    manifest = json.loads((svc_dir / "extract.json").read_text(encoding="utf-8"))
    pkg = f"{args.service}_service"
    if args.service != "catalog":
        # claim-based access policy comes from the kit; catalog keeps its own module (query scoping)
        SHARED["app.core.catalog_access"] = "mskit.auth.access"
    files = manifest["files"] + manifest.get("replicas", [])  # replicas: read-only copies of another service's reference models
    owned = {"app." + module_name(f) for f in files}
    for pkg_dir in {str(Path(f).parent).replace("\\", "/") for f in files}:
        parts = pkg_dir.split("/")
        for i in range(1, len(parts) + 1):
            owned.add("app." + ".".join(parts[:i]))
    leftovers: list[str] = []
    for rel in files:
        src = APP / rel
        dst = svc_dir / pkg / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        for parent in [dst.parent, *dst.parent.parents]:
            if parent == svc_dir / pkg or parent == svc_dir:
                break
            init = parent / "__init__.py"
            rel_init = init.relative_to(svc_dir / pkg).as_posix()
            if not init.exists() and rel_init not in files:   # a real package __init__ is copied, not stubbed
                init.write_text("", encoding="utf-8")
        if dst.exists() and not args.force:
            text = dst.read_text(encoding="utf-8")
        else:
            text = rewrite(src.read_text(encoding="utf-8"), owned, pkg)
            dst.write_text(text, encoding="utf-8")
        for n, line in enumerate(text.splitlines(), 1):
            if re.search(r"^\s*(from app[.\s]|import app\.)", line):
                leftovers.append(f"{rel}:{n}: {line.strip()}")
    (svc_dir / pkg / "__init__.py").touch()
    if leftovers:
        print("Imports that still point at the monolith (other services):")
        print("  " + "\n  ".join(leftovers))
    else:
        print(f"{args.service}: {len(files)} files, no monolith imports left")
    return 0


if __name__ == "__main__":
    sys.exit(main())
