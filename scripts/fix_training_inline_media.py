"""One-time remediation: replace inline base64 `data:` URIs stored in a
single Training's video_url/content_url/videos[]/documents[].url fields with
real uploaded files + short HTTP(S) upload URLs.

Context: normalize_authoring() (app/services/training_curriculum.py) now
converts inline data: URIs to upload URLs at save time, so this can no longer
happen for NEW saves. But a row written before that fix (e.g. the reported
training 5959a8c3-1777-40c3-bb1c-88caccbb759c) still has the raw base64
sitting in its `sections`/`documents` JSON until it's touched by this script
or re-saved through the app. This script is the safe, one-time, single-row
fix for already-affected rows — it does not touch the database schema and
does not touch any other training.

Reuses the EXACT same conversion logic the live write path already uses and
this session's tests already cover (_persist_inline_media /
_persist_inline_media_in_documents) — not a second, bespoke implementation.

SAFE BY DEFAULT: running with no flags only REPORTS what it would change.
Nothing is written to disk or the database unless --apply is passed.

Usage:
    # Inspect only — prints what would change, changes nothing:
    python scripts/fix_training_inline_media.py <training_id>

    # Apply the fix for real (writes files, updates only this one row):
    python scripts/fix_training_inline_media.py <training_id> --apply

    # Defaults to the specific reported training if no id is given:
    python scripts/fix_training_inline_media.py --apply
"""

import argparse
import sys
from copy import deepcopy
from uuid import UUID

from sqlalchemy.orm.attributes import flag_modified

from app.db.database import SessionLocal
from app.models.training_model import Training
from app.services.training_curriculum import (
    _persist_inline_media,
    _persist_inline_media_in_documents,
)

REPORTED_TRAINING_ID = "5959a8c3-1777-40c3-bb1c-88caccbb759c"


def _is_data_uri(value) -> bool:
    return isinstance(value, str) and value.startswith("data:")


def _scan_and_fix(sections: list, *, apply: bool) -> tuple[list, list[str]]:
    """Returns (possibly-updated sections, human-readable change log). Never
    mutates the input list in place — callers decide whether to keep the
    original or the fixed copy based on `apply`."""
    sections = deepcopy(sections or [])
    changes: list[str] = []

    for section in sections:
        for lesson in section.get("lessons") or section.get("items") or []:
            lesson_label = f"section={section.get('id')!r} lesson={lesson.get('id')!r} ({lesson.get('title')!r})"

            for field in ("content_url", "video_url"):
                value = lesson.get(field)
                if _is_data_uri(value):
                    size_kb = round(len(value) / 1024, 1)
                    changes.append(f"{lesson_label}: {field} is a data: URI (~{size_kb} KB)")
                    if apply:
                        lesson[field] = _persist_inline_media(value)

            videos = lesson.get("videos") or []
            if any(_is_data_uri(v) for v in videos):
                n = sum(1 for v in videos if _is_data_uri(v))
                changes.append(f"{lesson_label}: videos[] has {n} data: URI entr{'y' if n == 1 else 'ies'}")
                if apply:
                    lesson["videos"] = [_persist_inline_media(v) for v in videos]

            documents = lesson.get("documents") or []
            if any(isinstance(d, dict) and _is_data_uri(d.get("url")) for d in documents):
                n = sum(1 for d in documents if isinstance(d, dict) and _is_data_uri(d.get("url")))
                changes.append(f"{lesson_label}: documents[] has {n} data: URI url(s)")
                if apply:
                    lesson["documents"] = _persist_inline_media_in_documents(documents)

    return sections, changes


def _scan_and_fix_training_documents(documents: list, *, apply: bool) -> tuple[list, list[str]]:
    documents = deepcopy(documents or [])
    changes: list[str] = []
    if any(isinstance(d, dict) and _is_data_uri(d.get("url")) for d in documents):
        n = sum(1 for d in documents if isinstance(d, dict) and _is_data_uri(d.get("url")))
        changes.append(f"training-level documents[] has {n} data: URI url(s)")
        if apply:
            documents = _persist_inline_media_in_documents(documents)
    return documents, changes


def fix_training(training_id: str, *, apply: bool) -> int:
    db = SessionLocal()
    try:
        try:
            tid = UUID(str(training_id))
        except ValueError:
            print(f"'{training_id}' is not a valid UUID.", file=sys.stderr)
            return 2

        training = db.query(Training).filter(Training.id == tid).first()
        if not training:
            print(f"Training {tid} not found.", file=sys.stderr)
            return 2

        new_sections, section_changes = _scan_and_fix(training.sections, apply=apply)
        new_documents, doc_changes = _scan_and_fix_training_documents(training.documents, apply=apply)
        all_changes = section_changes + doc_changes

        if not all_changes:
            print(f"Training {tid}: no inline data: URIs found. Nothing to do.")
            return 0

        print(f"Training {tid} ('{training.title}') — {len(all_changes)} field(s) affected:")
        for line in all_changes:
            print(f"  - {line}")

        if not apply:
            print("\nDry run only — no files written, no database changes made.")
            print("Re-run with --apply to write the files and update this one row.")
            return 0

        training.sections = new_sections
        training.documents = new_documents
        flag_modified(training, "sections")
        flag_modified(training, "documents")
        db.commit()
        print(f"\nApplied. Training {tid} now stores upload URLs instead of inline data.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "training_id", nargs="?", default=REPORTED_TRAINING_ID,
        help=f"Training UUID to fix (default: the reported training, {REPORTED_TRAINING_ID})",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write files and update the database row. Without this flag, only reports what would change.",
    )
    args = parser.parse_args()
    sys.exit(fix_training(args.training_id, apply=args.apply))
