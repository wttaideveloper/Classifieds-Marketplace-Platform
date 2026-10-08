"""Find out why an Event media URL answers 404 {"detail": "File not found"}.

That one answer covers four different problems. Run this where the API runs (same database, same UPLOAD_DIR):

    python -m scripts.check_event_media 7ba4bf78-7c1c-466e-96c5-9c5929c7bb57.png
    python -m scripts.check_event_media <file> <file> ...
    python -m scripts.check_event_media --scan

In Docker:  docker exec -it <api-container> python -m scripts.check_event_media <file>

For each file it prints one verdict:
  no_database_record    no row in event_media: this API's database never saw the upload (wrong database, or the
                        upload went to a different environment)
  extension_mismatch    the row exists but was stored with another extension than the URL asks for
  file_missing_on_disk  row and name are fine but the file is not under UPLOAD_DIR/events: the storage the API
                        is using is not the storage the file was written to (volume not mounted, files lost on a
                        redeploy, or another server/replica received the upload)
  ok                    row and file exist; a 404 is then coming from somewhere else (e.g. the proxy)
  invalid_name          not <uuid>.<ext>

--scan compares every event_media row with the files on disk and lists the rows whose file is missing.
Read-only: nothing is changed.
"""
import argparse
import json

from app.db.database import SessionLocal
from app.services import event_media_service as media


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", help="<asset id>.<ext>, the last part of the media URL")
    parser.add_argument("--scan", action="store_true", help="compare all event_media rows with the files on disk")
    args = parser.parse_args()
    if not args.files and not args.scan:
        parser.error("give at least one <asset id>.<ext>, or --scan")

    worst = 0
    with SessionLocal() as db:
        for name in args.files:
            result = media.diagnose_asset_file(db, name)
            print(json.dumps(result, indent=2, default=str))
            worst = max(worst, 0 if result["verdict"] == "ok" else 1)
        if args.scan:
            report = media.scan_event_media(db)
            print(json.dumps(report, indent=2, default=str))
            if report["records_with_missing_file_used_by_an_event"]:
                worst = 1
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
