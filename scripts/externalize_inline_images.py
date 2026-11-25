"""Convert base64 images already saved on Products and Services into hosted files (app/services/inline_media.py).

    python scripts/externalize_inline_images.py            # dry run: lists what would change, writes nothing
    python scripts/externalize_inline_images.py --apply    # stores the files and rewrites the columns

Run it where the API runs (same DATABASE_URL, same UPLOAD_DIR volume) and with PUBLIC_API_BASE_URL / PUBLIC_MEDIA_BASE_URL set, so the URLs written are absolute.
Each row is its own transaction; a row with a bad image is reported and left exactly as it was. Running it twice is harmless (converted rows have no base64 left).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the files and update the rows (default: dry run)")
    args = parser.parse_args()

    from fastapi import HTTPException

    from app.db.database import SessionLocal
    from app.models.product_model import Product
    from app.models.service_model import Service
    from app.services import inline_media
    from app.utils.public_urls import public_media_base

    if not public_media_base():
        print("WARNING: PUBLIC_API_BASE_URL / PUBLIC_MEDIA_BASE_URL is not set: the stored URLs will be relative (/api/v1/products/upload/...).")
    totals = {"rows": 0, "images": 0, "failed_rows": 0}
    with SessionLocal() as db:
        for model, column in ((Product, "product_images"), (Service, "banner_image")):
            ids = [r[0] for r in db.query(model.id).filter(getattr(model, column).contains("base64,")).all()]
            for row_id in ids:
                row = db.get(model, row_id)
                value = getattr(row, column)
                if not inline_media.has_inline(value):
                    continue
                count = sum(1 for e in inline_media.entries(value) if e.lower().startswith("data:"))
                totals["rows"] += 1
                totals["images"] += count
                label = f"{model.__name__} {row_id} ({column}): {count} inline image(s), {len(value) // 1024} KB"
                if not args.apply:
                    print("would convert", label)
                    continue
                try:
                    setattr(row, column, inline_media.externalize(value))
                    db.commit()
                    print("converted", label)
                except HTTPException as exc:
                    db.rollback()
                    totals["failed_rows"] += 1
                    print(f"SKIPPED {label}: {exc.detail}")
    print(("Applied: " if args.apply else "Dry run: ") + ", ".join(f"{k}={v}" for k, v in totals.items()))
    return 1 if totals["failed_rows"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
