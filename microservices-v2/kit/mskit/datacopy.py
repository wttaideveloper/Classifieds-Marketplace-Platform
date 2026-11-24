"""Copy a service's own tables from the monolith database into the service's database.

    python -m <name>_service.copy_data --source-url postgresql://... [--execute] [--truncate-target]

* Read-only on the source: only ``SELECT`` statements are issued against it.
* Dry run by default: prints row counts per table and writes nothing. ``--execute`` writes.
* Copies only the tables the service owns, in foreign-key order, in batches, inside one
  transaction on the target (all or nothing). Columns are matched by name.
* Refuses to write into a target table that already has rows unless ``--truncate-target``.
* Never run against production without a backup and an agreed cut-over window (see
  docs/DATA_MIGRATION.md): rows written to the monolith after the copy are not carried over.
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import MetaData, create_engine, func, insert, select, text


def copy_tables(source_url: str, target_url: str, metadata: MetaData, tables: list[str], *, execute: bool, truncate: bool, batch: int = 1000) -> dict:
    src = create_engine(source_url)
    dst = create_engine(target_url)
    ordered = [t for t in metadata.sorted_tables if t.name in tables]
    missing = set(tables) - {t.name for t in ordered}
    if missing:
        raise SystemExit(f"tables not in this service's models: {sorted(missing)}")
    src_meta = MetaData()
    src_meta.reflect(bind=src, only=[t.name for t in ordered])
    report = {}
    with src.connect() as s:
        s.execute(text("SELECT 1"))
        counts = {t.name: s.execute(select(func.count()).select_from(src_meta.tables[t.name])).scalar() for t in ordered}
    report["source_rows"] = counts
    if not execute:
        report["mode"] = "dry-run (nothing written)"
        return report
    with dst.begin() as d, src.connect() as s:
        for t in ordered:
            existing = d.execute(select(func.count()).select_from(t)).scalar()
            if existing and not truncate:
                raise SystemExit(f"target table {t.name} already has {existing} rows (use --truncate-target)")
        if truncate:
            for t in reversed(ordered):
                d.execute(t.delete())
        for t in ordered:
            src_t = src_meta.tables[t.name]
            missing_cols = [c.name for c in t.columns if c.name not in src_t.columns]
            if missing_cols:
                raise SystemExit(f"source table {t.name} lacks columns {missing_cols}: schema versions differ")
            cols = [c.name for c in t.columns]
            # Read through the service's own column types (identical schema) so values arrive as the
            # model expects them (UUID, JSON, booleans) instead of the reflected database types.
            result = s.execution_options(stream_results=True).execute(select(*[t.c[c] for c in cols]))
            while True:
                rows = result.fetchmany(batch)
                if not rows:
                    break
                d.execute(insert(t), [dict(zip(cols, r)) for r in rows])
        copied = {t.name: d.execute(select(func.count()).select_from(t)).scalar() for t in ordered}
    report["target_rows"] = copied
    report["mode"] = "executed"
    report["verified"] = copied == counts
    return report


def cli(metadata: MetaData, tables: list[str], target_url: str, argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source-url", required=True, help="monolith database (read-only access is enough)")
    ap.add_argument("--target-url", default=target_url, help="this service's database (default: DATABASE_URL)")
    ap.add_argument("--execute", action="store_true", help="write; without it this is a dry run")
    ap.add_argument("--truncate-target", action="store_true")
    args = ap.parse_args(argv)
    report = copy_tables(args.source_url, args.target_url, metadata, tables, execute=args.execute, truncate=args.truncate_target)
    print(report)
    return 0 if report.get("verified", True) else 1


if __name__ == "__main__":
    sys.exit(cli(MetaData(), [], ""))
