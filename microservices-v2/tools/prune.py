"""Remove top-level functions / import lines from a copied module (AST-exact, decorators included).

    from prune import prune
    prune(path, functions=["search_events"], import_prefixes=["from app.schemas.event_schema"])

Used when a shared monolith module mixes several domains: each service keeps only its own part.
Prints what was removed; refuses names that do not exist (so a monolith change is noticed).
"""
from __future__ import annotations

import ast
from pathlib import Path


def prune(path, functions=(), import_prefixes=(), note: str | None = None) -> list[str]:
    path = Path(path)
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)
    spans = []
    found = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in functions:
            start = min([d.lineno for d in getattr(node, "decorator_list", [])] + [node.lineno]) - 1
            spans.append((start, node.end_lineno))
            found.add(node.name)
    missing = set(functions) - found
    if missing:
        raise SystemExit(f"{path.name}: not found: {sorted(missing)}")
    removed = sorted(found)
    keep = []
    skip_until = -1
    span_starts = {s: e for s, e in spans}
    i = 0
    while i < len(lines):
        if i in span_starts:
            i = span_starts[i]
            continue
        line = lines[i]
        if any(line.lstrip().startswith(p) for p in import_prefixes):
            # drop the import (with a parenthesised continuation if any)
            removed.append(line.strip())
            if "(" in line and ")" not in line:
                while i < len(lines) and ")" not in lines[i]:
                    i += 1
            i += 1
            continue
        keep.append(line)
        i += 1
    text = "".join(keep)
    if note:
        text = text.replace('"""', '"""' + note + "\n\n", 1) if text.lstrip().startswith('"""') else f"# {note}\n" + text
    path.write_text(text, encoding="utf-8")
    return removed


def copy_functions(src, names, *, header: str = "", rewrite: dict | None = None) -> str:
    """Source text of the named top-level functions/classes of ``src`` (AST-exact), with import rewrites."""
    src = Path(src)
    text = src.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    found = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in names:
            start = min([d.lineno for d in getattr(node, "decorator_list", [])] + [node.lineno]) - 1
            found[node.name] = "".join(lines[start:node.end_lineno])
    missing = set(names) - set(found)
    if missing:
        raise SystemExit(f"{src.name}: not found: {sorted(missing)}")
    body = "\n\n".join(found[n] for n in names)
    for old, new in (rewrite or {}).items():
        body = body.replace(old, new)
    return header + body
