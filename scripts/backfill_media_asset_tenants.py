"""Fill media_assets.tenant_id for pictures saved before the business was recorded.

Without a tenant the file cannot be fetched by the business's staff. The tenant is taken from an eBook that uses the picture, or from
the uploader's other pictures when they all belong to one business. Anything else is left alone: its uploader can still open it, and
the tenant is filled in at that moment.

Dry run by default (writes nothing):
    python -m scripts.backfill_media_asset_tenants
    python -m scripts.backfill_media_asset_tenants --apply
    python -m scripts.backfill_media_asset_tenants --apply --asset 64d0fe05-3709-4566-a3bd-319cc0c7a9b4
"""
import argparse
import logging

from app.db.database import SessionLocal
from app.services.media_service import backfill_tenants


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the resolved tenants (default: report only)")
    parser.add_argument("--asset", help="repair only this asset id")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    with SessionLocal() as db:
        counts = backfill_tenants(db, apply=args.apply, asset_id=args.asset)

    mode = "APPLIED" if args.apply else "DRY RUN (nothing written; add --apply)"
    print(f"{mode}: checked={counts['checked']} resolved={counts['resolved']} unresolved={counts['unresolved']} written={counts['written']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
