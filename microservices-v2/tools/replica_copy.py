"""Copy monolith model classes into a service as read-only replicas.

    from replica_copy import replica_module
    replica_module("event_aux_models.py", keep={"EventRegistration", "EventOrder"}, service="communication",
                   replicated_tables={...})

* keeps only the named classes (the tables this service replicates);
* drops ForeignKey(...) constraints that point at tables this service does not hold (form configurations,
  ...); the columns, types and indexes stay exactly the monolith's (schema-parity test), only that referential
  constraint is absent from the replica;
* rewrites imports like tools/extract.py.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

V2 = Path(__file__).resolve().parents[1]
APP = V2.parent / "app"
sys.path.insert(0, str(V2 / "tools"))
from extract import rewrite  # noqa: E402


def replica_module(model_file: str, *, keep: set, service: str, replicated_tables: set) -> Path:
    src = (APP / "models" / model_file).read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    drop_spans = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name not in keep:
            start = min([d.lineno for d in node.decorator_list] + [node.lineno]) - 1
            drop_spans.append((start, node.end_lineno))
    out, i = [], 0
    starts = dict(drop_spans)
    while i < len(lines):
        if i in starts:
            i = starts[i]
            continue
        out.append(lines[i])
        i += 1
    text = "".join(out)

    def strip_fk(m):
        table = m.group(2).split(".")[0]
        return m.group(0) if table in replicated_tables else ""

    # ForeignKey("table.col", ...) as a positional argument: remove it (and its trailing comma) when the table is absent
    text = re.sub(r'(\s*)ForeignKey\(\s*["\']([\w.]+)["\'][^()]*\)\s*,?', lambda m: strip_fk(m) if strip_fk(m) else m.group(1), text)
    pkg = f"{service}_service"
    text = rewrite(text, {"app.models." + model_file[:-3]}, pkg)
    text = ('"""v2 ' + service + ': READ-ONLY replica of ' + ", ".join(sorted(keep)) + ' (kept current by mskit.replication).\n'
            'Foreign keys to tables this service does not hold were dropped; columns, types and indexes are the monolith\'s."""\n') + text
    dst = V2 / "services" / service / pkg / "models" / model_file
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8")
    ast.parse(text)
    return dst
