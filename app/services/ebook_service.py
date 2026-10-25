"""eBook lessons: structured pages, a draft copy and a published copy, approval, and what learners do with a published eBook.

The author works on the *draft*. Saving replaces the whole draft (with a version check so two people cannot overwrite each other). To
go live the draft must pass validation, be sent for approval, and be approved by a Super Admin; approving makes a *published* copy.
Learners are only ever given that published copy, after the same enrolment and lock checks the rest of the course uses.

    draft -> pending_approval -> approved -> (publish) -> live
                  |
                  +-> changes_requested -> (author edits) -> pending_approval ...

A save after approval puts the draft back to `draft`; what is live does not change until the new draft is approved and published.
"""
import json
import logging
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.ebook_model import (
    MediaAsset, TrainingEbook, TrainingEbookAudit, TrainingEbookBookmark, TrainingEbookNote, TrainingEbookProgress, TrainingEbookVersion,
)
from app.models.training_model import TrainingEnrolment
from app.schemas.ebook_schema import EbookDocumentIn
from app.services import ebook_notifications, media_service
from app.utils.html_sanitize import clean_html_report

logger = logging.getLogger(__name__)

STATUS_LABELS = {"draft": "Draft", "pending_approval": "Pending Approval", "changes_requested": "Changes Requested", "approved": "Approved"}
MAX_BOOKMARKS, MAX_NOTES = 200, 500


def _now() -> datetime:
    return datetime.utcnow()


def _err(status: int, detail) -> HTTPException:
    return HTTPException(status_code=status, detail=detail)


def _plain(html: str | None) -> str:
    import re

    return re.sub(r"<[^>]*>", "", html or "").strip()


# ---------------------------------------------------------------- finding things and checking who may

@dataclass
class Context:
    training: object
    section_id: str
    lesson: dict
    row: TrainingEbook | None
    user: dict
    role: str


def _actor(user: dict) -> dict:
    return {"id": str(user.get("id")) if user.get("id") else None, "name": user.get("name") or user.get("email"), "role": user.get("role")}


def _uuid_or_none(value) -> UUID | None:
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


def _find_ebook_lesson(training, section_id: str, lesson_id: str) -> dict:
    from app.services.training_service import _find_lesson, _find_lesson_any_type

    section, lesson = _find_lesson(training, section_id, lesson_id)
    if lesson is None:
        found_section, lesson = _find_lesson_any_type(training, lesson_id)
        if lesson is None or str((found_section or {}).get("id")) != str(section_id):
            raise _err(404, "Lesson not found")
    if lesson.get("type") != "ebook":
        raise _err(409, "This lesson is not an eBook lesson. Create the lesson with type \"ebook\" first")
    return lesson


def authoring_context(db: Session, tid: UUID, section_id: str, lesson_id: str, user: dict, token: str | None, *, write: bool, lock: bool = False) -> Context:
    """Enterprise Admins and providers of the training's business may edit; a Super Admin may only look (they review)."""
    from app.repository.training_repo import get_training_by_id, require_training_owner
    from app.services.super_admin_identity import profile_status_is_active

    role = user.get("role")
    if role == "super_admin":
        if not profile_status_is_active(user):
            raise _err(403, "Not authorized")
        if write:
            raise _err(403, "Super Admins review eBooks. The author edits them")
        training = get_training_by_id(db, tid)
        if training is None:
            raise _err(404, "Training not found")
    elif role in ("admin", "provider"):
        training = require_training_owner(db, tid, user, access_token=token)
    else:
        raise _err(403, "Not authorized")
    lesson = _find_ebook_lesson(training, section_id, lesson_id)
    query = db.query(TrainingEbook).filter(TrainingEbook.training_id == tid, TrainingEbook.lesson_id == lesson_id)
    row = (query.with_for_update() if lock else query).first()
    return Context(training=training, section_id=section_id, lesson=lesson, row=row, user=user, role=role)


def _tenant_of(training) -> UUID | None:
    return (training.enterprise.tenant_id if getattr(training, "enterprise", None) else None) or training.tenant_id


def _audit(db: Session, row: TrainingEbook, action: str, user: dict, *, from_status: str | None = None, to_status: str | None = None, note: str | None = None) -> None:
    who = _actor(user)
    db.add(TrainingEbookAudit(ebook_id=row.id, training_id=row.training_id, lesson_id=row.lesson_id, action=action, version=row.draft_version,
                              from_status=from_status, to_status=to_status or row.status, note=(note or None) and note[:2000], actor_id=who["id"],
                              actor_name=who["name"], actor_role=who["role"]))


def _snapshot(db: Session, row: TrainingEbook, kind: str, user: dict) -> None:
    db.add(TrainingEbookVersion(ebook_id=row.id, version=row.draft_version, kind=kind, snapshot=deepcopy(row.draft), created_by=_uuid_or_none(user.get("id"))))


# ---------------------------------------------------------------- the document

def empty_document() -> dict:
    return {"subtitle": None, "author": None, "description": None, "cover_asset_id": None, "completion_rule": "read_to_end",
            "allow_bookmarks": True, "allow_notes": True, "pages": []}


def normalize_document(doc: EbookDocumentIn) -> tuple[dict, list[dict]]:
    """The cleaned, numbered document to store, and warnings (markup that was removed). Raises 422 for things that cannot be stored."""
    if len(doc.pages) > settings.EBOOK_MAX_PAGES:
        raise _err(422, f"An eBook can have at most {settings.EBOOK_MAX_PAGES} pages")
    pages = list(doc.pages)
    orders = [p.order for p in pages]
    if pages and all(o is not None for o in orders) and len(set(orders)) == len(orders):
        pages.sort(key=lambda p: p.order)
    seen_pages, seen_images, warnings, out_pages = set(), set(), [], []
    for index, page in enumerate(pages, 1):
        page_id = page.id or f"page_{uuid4().hex[:8]}"
        if page_id in seen_pages:
            raise _err(422, f"Two pages have the id {page_id}")
        seen_pages.add(page_id)
        raw_html = page.content_html or ""
        if len(raw_html.encode("utf-8")) > settings.EBOOK_MAX_PAGE_HTML_BYTES:
            raise _err(422, f"Page {index} is too long ({settings.EBOOK_MAX_PAGE_HTML_BYTES // 1000} KB of text is the most one page can hold)")
        html, notes = clean_html_report(raw_html, "ebook")
        if len(page.images) > settings.EBOOK_MAX_IMAGES_PER_PAGE:
            raise _err(422, f"Page {index} has more than {settings.EBOOK_MAX_IMAGES_PER_PAGE} pictures")
        images = []
        for image in page.images:
            image_id = image.id or f"image_{uuid4().hex[:8]}"
            if image_id in seen_images:
                raise _err(422, f"Two pictures have the id {image_id}")
            seen_images.add(image_id)
            images.append({**image.model_dump(), "id": image_id})
        entry = {"id": page_id, "order": index, "title": page.title or "", "content_html": html, "images": images}
        if notes:
            entry["html_notes"] = notes
            warnings.append({"page_id": page_id, "field": "content_html", "code": "unsafe_markup_removed", "message": "Unsupported markup was removed: " + "; ".join(notes)})
        out_pages.append(entry)
    stored = {"subtitle": doc.subtitle, "author": doc.author, "description": doc.description, "cover_asset_id": doc.cover_asset_id,
              "completion_rule": doc.completion_rule, "allow_bookmarks": doc.allow_bookmarks, "allow_notes": doc.allow_notes, "pages": out_pages}
    return stored, warnings


def _item(code: str, field: str, message: str, page_id=None, image_id=None) -> dict:
    return {"page_id": page_id, "image_id": image_id, "field": field, "code": code, "message": message}


def validate_document(db: Session, tenant_id: UUID | None, doc: dict) -> tuple[list[dict], list[dict]]:
    """(errors that stop it going for approval, warnings)."""
    errors, warnings = [], []
    pages = doc.get("pages") or []
    if not pages:
        errors.append(_item("no_pages", "pages", "Add at least one page"))
    ids = [p.get("id") for p in pages]
    if len(set(ids)) != len(ids):
        errors.append(_item("duplicate_page_ids", "pages", "Two pages have the same id"))
    if [p.get("order") for p in pages] != list(range(1, len(pages) + 1)):
        errors.append(_item("bad_order", "order", "The page order is not 1, 2, 3 ... without gaps. Save the eBook again"))
    if doc.get("completion_rule") not in ("read_to_end", "opened", "manual"):
        errors.append(_item("bad_completion_rule", "completion_rule", "The completion rule must be read_to_end, opened or manual"))
    asset_ids = {i.get("asset_id") for p in pages for i in p.get("images") or []}
    if doc.get("cover_asset_id"):
        asset_ids.add(doc["cover_asset_id"])
    assets = media_service.lookup(db, asset_ids)

    def usable(asset_id) -> str | None:
        asset = assets.get(str(asset_id)) if asset_id else None
        if asset is None or asset.is_deleted:
            return "The picture does not exist (it may have been deleted). Upload it again"
        if tenant_id is not None and asset.tenant_id is not None and asset.tenant_id != tenant_id:
            return "The picture belongs to another business"
        if asset.kind != "image":
            return "This file is not a picture"
        if not media_service.file_exists(asset):
            return "The picture file is missing from storage. Upload it again"
        return None

    if doc.get("cover_asset_id"):
        problem = usable(doc["cover_asset_id"])
        if problem:
            errors.append(_item("cover_unavailable", "cover_asset_id", f"The cover: {problem}"))
    for page in pages:
        pid = page.get("id")
        if not (page.get("title") or "").strip():
            errors.append(_item("page_title_missing", "title", "Every page needs a title", pid))
        images = page.get("images") or []
        if not _plain(page.get("content_html")) and not images:
            errors.append(_item("page_empty", "content_html", "This page is empty. Add text or a picture, or remove the page", pid))
        for note in page.get("html_notes") or []:
            errors.append(_item("invalid_html", "content_html", f"The text has unsupported markup ({note}). Save the page again from the editor", pid))
        image_ids = {i.get("id") for i in images}
        for image in images:
            iid = image.get("id")
            if not (image.get("alt_text") or "").strip() and not image.get("decorative"):
                errors.append(_item("alt_text_required", "alt_text", "Image alt text is required", pid, iid))
            problem = usable(image.get("asset_id"))
            if problem:
                errors.append(_item("asset_unavailable", "asset_id", problem, pid, iid))
            if image.get("placement") == "inline" and f'data-image-id="{iid}"' not in (page.get("content_html") or ""):
                warnings.append(_item("inline_placeholder_missing", "placement", "This picture is set to appear inline but the text has no place for it, so it will show after the text", pid, iid))
        html = page.get("content_html") or ""
        import re

        for placeholder in re.findall(r'data-image-id="([^"]+)"', html):
            if placeholder not in image_ids:
                warnings.append(_item("unknown_placeholder", "content_html", f"The text refers to a picture ({placeholder}) that is not on this page", pid))
    return errors, warnings


def _validation_response(errors, warnings, doc, version=None) -> dict:
    return {"valid": not errors, "errors": errors, "warnings": warnings, "page_count": len((doc or {}).get("pages") or []), "version": version}


def render_document(db: Session, doc: dict, *, learner: bool) -> dict:
    """The document for the apps: each picture gets its address and natural size. A learner never sees the editor-only notes."""
    out = deepcopy(doc)
    ids = {i.get("asset_id") for p in out.get("pages") or [] for i in p.get("images") or []}
    if out.get("cover_asset_id"):
        ids.add(out["cover_asset_id"])
    assets = media_service.lookup(db, ids)

    def describe(asset_id) -> dict:
        asset = assets.get(str(asset_id)) if asset_id else None
        if asset is None or asset.is_deleted:
            return {"url": None, "natural_width": None, "natural_height": None, "available": False}
        return {"url": media_service.file_url(asset), "natural_width": asset.width, "natural_height": asset.height, "available": True}

    out["cover_url"] = describe(out.get("cover_asset_id"))["url"] if out.get("cover_asset_id") else None
    for page in out.get("pages") or []:
        if learner:
            page.pop("html_notes", None)
            # Published snapshots may predate the current editor or have been
            # imported outside it. Never assume persisted markup is safe.
            page["content_html"] = clean_html_report(page.get("content_html"), "ebook")[0]
        for image in page.get("images") or []:
            image.update(describe(image.get("asset_id")))
    return out


def _page_index(doc: dict) -> dict[str, int]:
    return {p["id"]: i for i, p in enumerate(doc.get("pages") or [], 1)}


def diff_summary(draft: dict | None, published: dict | None) -> dict:
    draft_pages = {p["id"]: p for p in (draft or {}).get("pages") or []}
    live_pages = {p["id"]: p for p in (published or {}).get("pages") or []}
    changed = [pid for pid in draft_pages if pid in live_pages and {k: draft_pages[pid].get(k) for k in ("title", "content_html", "images")} !=
               {k: live_pages[pid].get(k) for k in ("title", "content_html", "images")}]
    meta = [k for k in ("subtitle", "author", "description", "cover_asset_id", "completion_rule", "allow_bookmarks", "allow_notes")
            if (draft or {}).get(k) != (published or {}).get(k)]
    return {"pages_added": [p for p in draft_pages if p not in live_pages], "pages_removed": [p for p in live_pages if p not in draft_pages], "pages_changed": changed,
            "order_changed": [p for p in draft_pages if p in live_pages] != [p for p in live_pages if p in draft_pages], "details_changed": meta,
            "is_first_version": published is None}


def has_unpublished_changes(row: TrainingEbook | None) -> bool:
    return bool(row) and (row.published is None or row.published_version != row.draft_version)


def actions_for(row: TrainingEbook | None, role: str) -> list[str]:
    if role == "super_admin":
        out = ["review"] if row is not None and row.status == "pending_approval" else []
        return out + (["history"] if row else [])
    status = row.status if row else "draft"
    out = []
    if status != "pending_approval":
        out.append("save")
        out.append("reorder")
    out.append("validate")
    if row is not None and status in ("draft", "changes_requested") and has_unpublished_changes(row):
        out.append("submit")
    if status == "pending_approval":
        out.append("withdraw")
    if status == "approved" and has_unpublished_changes(row):
        out.append("publish")
    if row is not None and row.published is not None:
        out.append("unpublish")
    if row is not None:
        out.append("history")
    return out


def _person(user_id, name) -> dict | None:
    return {"id": str(user_id) if user_id else None, "name": name} if (user_id or name) else None


def view_of(db: Session, ctx: Context, *, imported: bool = False, doc: dict | None = None) -> dict:
    row = ctx.row
    document = doc if doc is not None else (row.draft if row else empty_document())
    return {
        "id": row.id if row else None, "training_id": ctx.training.id, "section_id": ctx.section_id, "lesson_id": str(ctx.lesson.get("id")),
        "version": row.draft_version if row else 0, "status": row.status if row else "draft", "status_label": STATUS_LABELS[row.status if row else "draft"],
        "published_version": row.published_version if row else None, "published_at": row.published_at if row else None,
        "has_unpublished_changes": has_unpublished_changes(row) if row else False, "reviewer_note": row.reviewer_note if row else None,
        "submitted_at": row.submitted_at if row else None, "reviewed_at": row.reviewed_at if row else None, "updated_at": row.updated_at if row else None,
        "updated_by": _person(row.updated_by, row.updated_by_name) if row else None, "actions": actions_for(row, ctx.role),
        "imported_from_lesson_content": imported, "ebook": render_document(db, document, learner=False),
    }


def _legacy_document(lesson: dict) -> dict | None:
    """The older way: the whole eBook as JSON in the lesson's content field."""
    raw = lesson.get("content")
    if not isinstance(raw, str) or not raw.strip().startswith("{"):
        return None
    try:
        parsed = json.loads(raw)
        body = parsed.get("ebook") if isinstance(parsed, dict) and isinstance(parsed.get("ebook"), dict) else parsed
        if not isinstance(body, dict) or not isinstance(body.get("pages"), list):
            return None
        keep = {k: body[k] for k in EbookDocumentIn.model_fields if k in body}
        keep["pages"] = [{k: v for k, v in p.items() if k in ("id", "order", "title", "content_html", "images")} for p in keep["pages"] if isinstance(p, dict)]
        for page in keep["pages"]:
            page["images"] = [{k: v for k, v in i.items() if k in ("id", "asset_id", "alt_text", "caption", "width", "fit", "aspect_ratio", "position", "decorative", "placement")}
                              for i in page.get("images") or [] if isinstance(i, dict) and i.get("asset_id")]
        stored, _ = normalize_document(EbookDocumentIn.model_validate(keep))
        return stored
    except Exception:
        return None


# ---------------------------------------------------------------- the author: read, save, reorder, validate

def get_for_authoring(db: Session, tid: UUID, section_id: str, lesson_id: str, user: dict, token: str | None) -> dict:
    ctx = authoring_context(db, tid, section_id, lesson_id, user, token, write=False)
    if ctx.row is None:
        legacy = _legacy_document(ctx.lesson)
        if legacy is not None:
            return view_of(db, ctx, imported=True, doc=legacy)
    return view_of(db, ctx)


def _check_version(row: TrainingEbook | None, base_version: int | None) -> None:
    current = row.draft_version if row else 0
    if base_version is not None and base_version != current:
        raise _err(409, {"code": "VERSION_CONFLICT", "message": "Someone saved a newer version while you were editing. Load it, then apply your changes",
                         "current_version": current, "your_version": base_version,
                         "updated_at": row.updated_at.isoformat() if row and row.updated_at else None, "updated_by": row.updated_by_name if row else None})


def _touch(row: TrainingEbook, user: dict) -> None:
    row.updated_by, row.updated_by_name = _uuid_or_none(user.get("id")), _actor(user)["name"]


def save_draft(db: Session, tid: UUID, section_id: str, lesson_id: str, user: dict, token: str | None, body) -> dict:
    ctx = authoring_context(db, tid, section_id, lesson_id, user, token, write=True, lock=True)
    row = ctx.row
    if row is not None and row.status == "pending_approval":
        raise _err(409, {"code": "IN_REVIEW", "message": "This eBook is waiting for approval and cannot be changed. Withdraw it first, or wait for the decision"})
    _check_version(row, body.base_version)
    document, warnings = normalize_document(body.ebook)
    before = row.status if row else None
    if row is None:
        row = TrainingEbook(training_id=tid, tenant_id=_tenant_of(ctx.training), section_id=section_id, lesson_id=lesson_id, draft=document, draft_version=1,
                            status="draft", created_by=_uuid_or_none(user.get("id")))
        db.add(row)
        _touch(row, user)
        db.flush()
        _audit(db, row, "created", user, to_status="draft")
    else:
        row.draft, row.draft_version = document, row.draft_version + 1
        if row.status == "approved":
            row.status = "draft"                      # an approval is for the content that was approved; this is new content
        _touch(row, user)
        _audit(db, row, "saved", user, from_status=before)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "lesson_id": lesson_id, "version": row.draft_version, "status": row.status, "updated_at": row.updated_at,
            "published_version": row.published_version, "has_unpublished_changes": has_unpublished_changes(row), "warnings": warnings}


def reorder_pages(db: Session, tid: UUID, section_id: str, lesson_id: str, user: dict, token: str | None, body) -> dict:
    ctx = authoring_context(db, tid, section_id, lesson_id, user, token, write=True, lock=True)
    row = ctx.row
    if row is None:
        raise _err(404, "Nothing is saved for this eBook yet")
    if row.status == "pending_approval":
        raise _err(409, {"code": "IN_REVIEW", "message": "This eBook is waiting for approval and cannot be changed"})
    _check_version(row, body.base_version)
    pages = {p["id"]: p for p in row.draft.get("pages") or []}
    if len(set(body.page_ids)) != len(body.page_ids) or set(body.page_ids) != set(pages):
        raise _err(422, "Send every page id exactly once")
    document = deepcopy(row.draft)
    document["pages"] = [{**pages[pid], "order": index} for index, pid in enumerate(body.page_ids, 1)]
    before = row.status
    row.draft, row.draft_version = document, row.draft_version + 1
    if row.status == "approved":
        row.status = "draft"
    _touch(row, user)
    _audit(db, row, "reordered", user, from_status=before)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "lesson_id": lesson_id, "version": row.draft_version, "status": row.status, "updated_at": row.updated_at, "page_ids": body.page_ids,
            "published_version": row.published_version, "has_unpublished_changes": has_unpublished_changes(row)}


def validate(db: Session, tid: UUID, section_id: str, lesson_id: str, user: dict, token: str | None, body) -> dict:
    ctx = authoring_context(db, tid, section_id, lesson_id, user, token, write=False)
    if body is not None and body.ebook is not None:
        document, _ = normalize_document(body.ebook)
        errors, warnings = validate_document(db, _tenant_of(ctx.training), document)
        return _validation_response(errors, warnings, document, None)
    if ctx.row is None:
        legacy = _legacy_document(ctx.lesson)
        document = legacy if legacy is not None else empty_document()
        errors, warnings = validate_document(db, _tenant_of(ctx.training), document)
        return _validation_response(errors, warnings, document, 0)
    errors, warnings = validate_document(db, ctx.row.tenant_id, ctx.row.draft)
    return _validation_response(errors, warnings, ctx.row.draft, ctx.row.draft_version)


# ---------------------------------------------------------------- the approval trail

def submit(db: Session, tid: UUID, section_id: str, lesson_id: str, user: dict, token: str | None, body) -> dict:
    ctx = authoring_context(db, tid, section_id, lesson_id, user, token, write=True, lock=True)
    row = ctx.row
    if row is None:
        raise _err(404, "Nothing is saved for this eBook yet")
    if row.status == "pending_approval":
        raise _err(409, "This eBook is already waiting for approval")
    if row.status == "approved":
        raise _err(409, "This eBook is already approved. Publish it")
    if not has_unpublished_changes(row):
        raise _err(409, "There are no changes to send: the live eBook is already this version")
    _check_version(row, getattr(body, "base_version", None))
    errors, warnings = validate_document(db, row.tenant_id, row.draft)
    if errors:
        raise _err(422, {"code": "EBOOK_INVALID", "message": "Fix these problems before sending the eBook for approval", "errors": errors})
    before = row.status
    row.status, row.submitted_at, row.submitted_by, row.reviewer_note = "pending_approval", _now(), _uuid_or_none(user.get("id")), None
    _snapshot(db, row, "submitted", user)
    _audit(db, row, "submitted", user, from_status=before, note=getattr(body, "note", None))
    db.commit()
    ebook_notifications.notify(row.id, "submitted", access_token=token)
    db.refresh(row)
    return view_of(db, authoring_context(db, tid, section_id, lesson_id, user, token, write=False))


def withdraw(db: Session, tid: UUID, section_id: str, lesson_id: str, user: dict, token: str | None) -> dict:
    ctx = authoring_context(db, tid, section_id, lesson_id, user, token, write=True, lock=True)
    row = ctx.row
    if row is None or row.status != "pending_approval":
        raise _err(409, "This eBook is not waiting for approval")
    row.status = "draft"
    _audit(db, row, "withdrawn", user, from_status="pending_approval", to_status="draft")
    db.commit()
    return view_of(db, authoring_context(db, tid, section_id, lesson_id, user, token, write=False))


def _super_admin_context(db: Session, tid: UUID, section_id: str, lesson_id: str, user: dict, token: str | None, *, lock: bool = False) -> Context:
    from app.services.super_admin_identity import profile_status_is_active

    if user.get("role") != "super_admin" or not profile_status_is_active(user):
        raise _err(403, "Only a Super Admin can review an eBook")
    return authoring_context(db, tid, section_id, lesson_id, user, token, write=False, lock=lock)


def review(db: Session, tid: UUID, section_id: str, lesson_id: str, user: dict, token: str | None, body) -> dict:
    ctx = _super_admin_context(db, tid, section_id, lesson_id, user, token, lock=True)
    row = ctx.row
    if row is None or row.status != "pending_approval":
        raise _err(409, f"Only an eBook that is waiting for approval can be reviewed. This one is {STATUS_LABELS.get(row.status, 'not saved').lower() if row else 'not saved'}")
    note = " ".join((body.note or "").split()) or None
    if body.action == "request_changes":
        if not note or len(note) < 3:
            raise _err(422, "Tell the author what to change (at least 3 characters)")
        row.status, row.reviewer_note = "changes_requested", note
        row.reviewed_at, row.reviewed_by = _now(), _uuid_or_none(user.get("id"))
        _audit(db, row, "changes_requested", user, from_status="pending_approval", note=note)
        db.commit()
        ebook_notifications.notify(row.id, "changes_requested", access_token=token, reason=note)
        return review_view(db, tid, section_id, lesson_id, user, token, include_drafts=True)
    errors, _ = validate_document(db, row.tenant_id, row.draft)
    if errors:
        raise _err(422, {"code": "EBOOK_INVALID", "message": "The eBook no longer passes validation (a picture may have been removed). Ask for changes", "errors": errors})
    row.status, row.reviewer_note = "approved", note
    row.reviewed_at, row.reviewed_by = _now(), _uuid_or_none(user.get("id"))
    _snapshot(db, row, "approved", user)
    _audit(db, row, "approved", user, from_status="pending_approval", note=note)
    if body.publish:
        _publish_now(db, row, user)
    db.commit()
    ebook_notifications.notify(row.id, "approved", access_token=token, published=bool(body.publish))
    return review_view(db, tid, section_id, lesson_id, user, token, include_drafts=True)


def _publish_now(db: Session, row: TrainingEbook, user: dict) -> None:
    row.published, row.published_version = deepcopy(row.draft), row.draft_version
    row.published_at, row.published_by = _now(), _uuid_or_none(user.get("id"))
    _snapshot(db, row, "published", user)
    _audit(db, row, "published", user, to_status=row.status)


def publish(db: Session, tid: UUID, section_id: str, lesson_id: str, user: dict, token: str | None) -> dict:
    """Makes an approved eBook live. The Enterprise Admin who owns it, or a Super Admin, may do this; nothing unapproved can be published."""
    from app.services.super_admin_identity import profile_status_is_active

    is_super = user.get("role") == "super_admin" and profile_status_is_active(user)
    ctx = authoring_context(db, tid, section_id, lesson_id, user, token, write=False, lock=True)
    row = ctx.row
    if row is None or row.status != "approved":
        raise _err(409, "Only an approved eBook can be published. Send it for approval first")
    if not has_unpublished_changes(row):
        raise _err(409, "This version is already live")
    _publish_now(db, row, user)
    db.commit()
    return review_view(db, tid, section_id, lesson_id, user, token, include_drafts=True) if is_super else view_of(db, authoring_context(db, tid, section_id, lesson_id, user, token, write=False))


def unpublish(db: Session, tid: UUID, section_id: str, lesson_id: str, user: dict, token: str | None) -> dict:
    ctx = authoring_context(db, tid, section_id, lesson_id, user, token, write=False, lock=True)
    row = ctx.row
    if row is None or row.published is None or row.published_version is None:
        raise _err(409, "This eBook is not live")
    row.published, row.published_version, row.published_at = None, None, None
    _audit(db, row, "unpublished", user)
    db.commit()
    ctx.row = row
    return view_of(db, ctx)


def history(db: Session, tid: UUID, section_id: str, lesson_id: str, user: dict, token: str | None) -> dict:
    ctx = authoring_context(db, tid, section_id, lesson_id, user, token, write=False)
    if ctx.row is None:
        return {"items": [], "versions": [], "total": 0}
    items = db.query(TrainingEbookAudit).filter(TrainingEbookAudit.ebook_id == ctx.row.id).order_by(TrainingEbookAudit.created_at.desc(), TrainingEbookAudit.id).limit(500).all()
    versions = db.query(TrainingEbookVersion).filter(TrainingEbookVersion.ebook_id == ctx.row.id).order_by(TrainingEbookVersion.created_at.desc()).limit(200).all()
    return {"items": items, "total": len(items),
            "versions": [{"version": v.version, "kind": v.kind, "created_by": str(v.created_by) if v.created_by else None, "created_at": v.created_at} for v in versions]}


def get_version(db: Session, tid: UUID, section_id: str, lesson_id: str, version: int, kind: str | None, user: dict, token: str | None) -> dict:
    ctx = authoring_context(db, tid, section_id, lesson_id, user, token, write=False)
    if ctx.row is None:
        raise _err(404, "Version not found")
    query = db.query(TrainingEbookVersion).filter(TrainingEbookVersion.ebook_id == ctx.row.id, TrainingEbookVersion.version == version)
    if kind:
        query = query.filter(TrainingEbookVersion.kind == kind)
    found = query.order_by(TrainingEbookVersion.created_at.desc()).first()
    if found is None:
        raise _err(404, "That version was not kept. Versions are kept when an eBook is sent for approval, approved or published")
    return {"version": found.version, "kind": found.kind, "created_at": found.created_at, "ebook": render_document(db, found.snapshot, learner=False)}


def restore_version(db: Session, tid: UUID, section_id: str, lesson_id: str, version: int, user: dict, token: str | None) -> dict:
    """Copies a kept version back into the draft as a new version."""
    ctx = authoring_context(db, tid, section_id, lesson_id, user, token, write=True, lock=True)
    row = ctx.row
    if row is None:
        raise _err(404, "Nothing is saved for this eBook yet")
    if row.status == "pending_approval":
        raise _err(409, {"code": "IN_REVIEW", "message": "This eBook is waiting for approval and cannot be changed"})
    found = db.query(TrainingEbookVersion).filter(TrainingEbookVersion.ebook_id == row.id, TrainingEbookVersion.version == version).order_by(TrainingEbookVersion.created_at.desc()).first()
    if found is None:
        raise _err(404, "That version was not kept")
    before = row.status
    row.draft, row.draft_version = deepcopy(found.snapshot), row.draft_version + 1
    if row.status == "approved":
        row.status = "draft"
    _touch(row, user)
    _audit(db, row, "restored", user, from_status=before, note=f"Restored version {version}")
    db.commit()
    ctx.row = row
    return view_of(db, ctx)


# ---------------------------------------------------------------- the Super Admin's view

def review_view(db: Session, tid: UUID, section_id: str, lesson_id: str, user: dict, token: str | None, *, include_drafts: bool) -> dict:
    ctx = _super_admin_context(db, tid, section_id, lesson_id, user, token)
    row = ctx.row
    if row is None:
        raise _err(404, "Nothing is saved for this eBook yet")
    entries = db.query(TrainingEbookAudit).filter(TrainingEbookAudit.ebook_id == row.id).order_by(TrainingEbookAudit.created_at.desc(), TrainingEbookAudit.id).limit(20).all()
    submitted_by = None
    if row.submitted_by:
        last = next((e for e in db.query(TrainingEbookAudit).filter(TrainingEbookAudit.ebook_id == row.id, TrainingEbookAudit.action == "submitted")
                     .order_by(TrainingEbookAudit.created_at.desc()).limit(1)), None)
        submitted_by = _person(row.submitted_by, last.actor_name if last else None)
    out = {
        "training_id": tid, "training_title": ctx.training.title, "section_id": section_id, "lesson_id": lesson_id, "lesson_title": ctx.lesson.get("title"),
        "status": row.status, "status_label": STATUS_LABELS[row.status], "version": row.draft_version, "published_version": row.published_version,
        "reviewer_note": row.reviewer_note, "submitted_at": row.submitted_at, "submitted_by": submitted_by, "last_editor": _person(row.updated_by, row.updated_by_name),
        "last_updated": row.updated_at, "published": render_document(db, row.published, learner=False) if row.published else None,
        "draft": None, "validation": None, "changes": None, "history": entries, "actions": actions_for(row, "super_admin"),
    }
    if include_drafts:
        errors, warnings = validate_document(db, row.tenant_id, row.draft)
        out.update({"draft": render_document(db, row.draft, learner=False), "validation": _validation_response(errors, warnings, row.draft, row.draft_version),
                    "changes": diff_summary(row.draft, row.published)})
    return out


def review_queue(db: Session, user: dict, page: int, page_size: int) -> dict:
    from app.models.training_model import Training
    from app.services.super_admin_identity import profile_status_is_active

    if user.get("role") != "super_admin" or not profile_status_is_active(user):
        raise _err(403, "Only a Super Admin can review eBooks")
    query = db.query(TrainingEbook).filter(TrainingEbook.status == "pending_approval")
    total = query.count()
    rows = query.order_by(TrainingEbook.submitted_at.asc(), TrainingEbook.id).offset((page - 1) * page_size).limit(page_size).all()
    items = []
    for row in rows:
        training = db.get(Training, row.training_id)
        lesson_title = next((l.get("title") for s in (training.sections if training else None) or [] for l in s.get("lessons", []) or [] if l.get("id") == row.lesson_id), None)
        last = db.query(TrainingEbookAudit).filter(TrainingEbookAudit.ebook_id == row.id, TrainingEbookAudit.action == "submitted").order_by(TrainingEbookAudit.created_at.desc()).first()
        items.append({"training_id": row.training_id, "training_title": training.title if training else None, "section_id": row.section_id, "lesson_id": row.lesson_id,
                      "lesson_title": lesson_title, "version": row.draft_version, "page_count": len(row.draft.get("pages") or []), "submitted_at": row.submitted_at,
                      "submitted_by": _person(row.submitted_by, last.actor_name if last else None)})
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ---------------------------------------------------------------- the learner

@dataclass
class LearnerContext:
    training: object
    lesson: dict
    row: TrainingEbook
    document: dict
    email: str | None
    is_staff: bool


def learner_context(db: Session, tid: UUID, lesson_id: str, user: dict, token: str | None = None, *, full_gate: bool, learners_only: bool = False) -> LearnerContext:
    """Who may read the published eBook: an enrolled learner whose lesson is unlocked, or the training's own staff / a Super Admin looking
    at what learners see. Nothing but the published copy is ever returned."""
    from app.repository.training_repo import get_training_by_id, require_training_owner
    from app.services import training_service as ts

    role = user.get("role")
    email = user.get("email")
    training = get_training_by_id(db, tid)
    if training is None:
        raise _err(404, "Training not found")
    _, lesson = ts._find_lesson_any_type(training, lesson_id)
    if lesson is None:
        raise _err(404, "Lesson not found")
    if lesson.get("type") != "ebook":
        raise _err(409, "This lesson is not an eBook")
    is_staff = role in ("admin", "provider", "super_admin")
    if is_staff:
        if learners_only:
            raise _err(403, "Reading progress, bookmarks and notes are for enrolled learners")
        if role in ("admin", "provider"):
            require_training_owner(db, tid, user, access_token=token)
    else:
        if lesson.get("is_draft") and not lesson.get("is_preview"):
            raise _err(404, "This eBook is not available")
        if full_gate:
            content = ts.get_secure_training_content_service(db, tid, user)       # enrolment, training status, release rules and locks, exactly as the course shows them
            payload = next((l for s in content.get("sections", []) for l in s.get("lessons", []) if str(l.get("id")) == str(lesson_id)), None)
            if payload is None:
                raise _err(404, "This eBook is not available")
            if payload.get("is_locked"):
                raise _err(403, {"code": "LESSON_LOCKED", "message": "This lesson is locked. Finish the earlier lessons first"})
        else:
            enrolled = db.query(TrainingEnrolment.id).filter(TrainingEnrolment.training_id == tid, TrainingEnrolment.participant_email == email,
                                                           TrainingEnrolment.status.in_(ts.ACTIVE_ENROLMENT_STATUSES)).first() if email else None
            if not enrolled:
                raise _err(403, "Enrolled participants only")
            if training.status in ("draft", "cancelled", "archived"):
                raise _err(403, f"Content not available. The training is {training.status}")
    row = db.query(TrainingEbook).filter(TrainingEbook.training_id == tid, TrainingEbook.lesson_id == lesson_id).first()
    if row is None or row.published is None:
        if is_staff:
            # learners only ever see the published copy; tell the author why their own preview is empty
            if row is None:
                raise _err(404, "Nothing is published: no eBook has been saved for this lesson through the eBook API yet")
            raise _err(404, f"Nothing is published yet. The eBook is saved as a draft (status: {row.status}); it must be sent for approval and published before this endpoint returns it")
        raise _err(404, "This eBook is not available yet")
    return LearnerContext(training=training, lesson=lesson, row=row, document=row.published, email=email, is_staff=is_staff)


def learner_view(db: Session, tid: UUID, lesson_id: str, user: dict, token: str | None) -> dict:
    ctx = learner_context(db, tid, lesson_id, user, token, full_gate=True)
    doc = render_document(db, ctx.document, learner=True)
    pages = []
    for page in sorted(doc.get("pages") or [], key=lambda p: p.get("order", 0)):
        images = page.get("images") or []
        blocks = []
        def image_block(image):
            ratio = image.get("aspect_ratio") or "original"
            if ratio != "original":
                match = re.fullmatch(r"(\d{1,2}):(\d{1,2})", str(ratio))
                if not match or int(match.group(1)) == 0 or int(match.group(2)) == 0:
                    ratio = "original"
            return {**image, "type": "image", "width": image.get("width") if image.get("width") in ("small", "medium", "full") else "full",
                    "fit": image.get("fit") if image.get("fit") in ("cover", "contain") else "cover", "aspect_ratio": ratio}
        blocks.extend(image_block(i) for i in images if i.get("placement") == "before_content")
        html = page.get("content_html") or ""
        inline = {str(i.get("id")): i for i in images if i.get("placement") == "inline"}
        used = set()
        pattern = re.compile(r'<(div|figure|p)\b[^>]*\bdata-image-id="([A-Za-z0-9_-]{1,64})"[^>]*>\s*</\1>', re.IGNORECASE)
        cursor = 0
        def text_block(fragment):
            cleaned = clean_html_report(fragment, "ebook_reader")[0]
            if cleaned:
                blocks.append({"id": f'{page["id"]}_text_{len(blocks) + 1}', "type": "text", "content_html": cleaned})
        for match in pattern.finditer(html):
            image_id = match.group(2)
            if image_id not in inline or image_id in used:
                continue
            text_block(html[cursor:match.start()])
            blocks.append(image_block(inline[image_id]))
            used.add(image_id)
            cursor = match.end()
        text_block(html[cursor:])
        blocks.extend(image_block(i) for i in images if i.get("placement") != "before_content" and str(i.get("id")) not in used)
        for number, block in enumerate(blocks, 1):
            block["order"] = number
        pages.append({"id": page["id"], "order": page.get("order"), "title": page.get("title"), "blocks": blocks})
    rec = _record(db, ctx, user, create=False) if ctx.email and not ctx.is_staff else None
    if rec and rec.version_read != ctx.row.published_version:
        _reconcile_progress(ctx, rec)
        db.commit()
    return {"id": ctx.row.id, "training_id": tid, "lesson_id": lesson_id, "title": ctx.lesson.get("title"), "version": ctx.row.published_version,
            "published_at": ctx.row.published_at, "page_count": len(pages), "ebook": doc, "published_version": ctx.row.published_version,
            "subtitle": doc.get("subtitle"), "author": doc.get("author"), "description": doc.get("description"), "cover_url": doc.get("cover_url"),
            "completion_rule": doc.get("completion_rule", "read_to_end"), "allow_bookmarks": doc.get("allow_bookmarks", True),
            "allow_notes": doc.get("allow_notes", True), "current_page_id": rec.last_page_id if rec else None, "pages": pages}


def _progress_out(db: Session, ctx: LearnerContext, rec: TrainingEbookProgress | None) -> dict:
    from app.models.training_model import TrainingProgress

    pages = ctx.document.get("pages") or []
    index = _page_index(ctx.document)
    lesson_done = False
    if ctx.email:
        prog = db.query(TrainingProgress).filter(TrainingProgress.training_id == ctx.training.id, TrainingProgress.participant_email == ctx.email).first()
        lesson_done = bool(prog and str(ctx.lesson.get("id")) in set(prog.lessons_completed or []))
    if rec is None:
        return {"published_version": ctx.row.published_version, "last_page_id": None, "last_page_number": None, "last_scroll_position": None, "progress_percent": 0, "completed": False, "completed_at": None,
                "page_count": len(pages), "version_read": None, "lesson_completed": lesson_done}
    still_there = rec.last_page_id in index
    return {"published_version": ctx.row.published_version, "last_page_id": rec.last_page_id if still_there else None, "last_page_number": index.get(rec.last_page_id) if still_there else None,
            "last_scroll_position": rec.last_scroll_position if still_there else None, "progress_percent": rec.progress_percent, "completed": bool(rec.completed and lesson_done),
            "completed_at": rec.completed_at, "page_count": len(pages), "version_read": rec.version_read, "lesson_completed": lesson_done}


def _record(db: Session, ctx: LearnerContext, user: dict, *, create: bool) -> TrainingEbookProgress | None:
    if create:
        # Lock the existing enrolment as well as the progress row: the latter
        # cannot be locked before the first progress record has been inserted.
        db.query(TrainingEnrolment).filter(TrainingEnrolment.training_id == ctx.training.id,
                                           TrainingEnrolment.participant_email == ctx.email).with_for_update().first()
    rec = db.query(TrainingEbookProgress).filter(TrainingEbookProgress.training_id == ctx.training.id, TrainingEbookProgress.lesson_id == str(ctx.lesson.get("id")),
                                                 TrainingEbookProgress.participant_email == ctx.email).with_for_update().first()
    if rec is None and create:
        rec = TrainingEbookProgress(training_id=ctx.training.id, lesson_id=str(ctx.lesson.get("id")), participant_email=ctx.email, user_id=_uuid_or_none(user.get("id")),
                                    progress_percent=0)
        db.add(rec)
        db.flush()
    return rec


def get_progress(db: Session, tid: UUID, lesson_id: str, user: dict, token: str | None) -> dict:
    ctx = learner_context(db, tid, lesson_id, user, token, full_gate=True, learners_only=True)
    rec = _record(db, ctx, user, create=False)
    if rec and rec.version_read != ctx.row.published_version:
        _reconcile_progress(ctx, rec)
        db.commit()
    return _progress_out(db, ctx, rec)


def _reconcile_progress(ctx: LearnerContext, rec: TrainingEbookProgress) -> None:
    index = _page_index(ctx.document)
    if rec.last_page_id not in index:
        rec.last_page_id = next(iter(index), None)
        rec.last_scroll_position = None
    rec.last_page_number = index.get(rec.last_page_id)
    rec.progress_percent = int(rec.last_page_number * 100 / len(index)) if rec.last_page_number and index else 0
    rec.version_read = ctx.row.published_version


def _count_in_course(db: Session, ctx: LearnerContext, notifications: list) -> None:
    """Apply course completion in the same transaction as ebook progress."""
    from app.services.training_service import complete_lesson_service

    complete_lesson_service(db, ctx.training.id, str(ctx.lesson.get("id")), ctx.email, commit=False, deferred_notifications=notifications)


def _send_completion_notifications(db: Session, notifications: list) -> None:
    from app.services import training_notifications
    for training, tid, email, url in notifications:
        try:
            enrol = db.query(TrainingEnrolment).filter(TrainingEnrolment.training_id == tid, TrainingEnrolment.participant_email == email).first()
            training_notifications.notify_certificate_ready(training, email=email, name=getattr(enrol, "participant_name", None),
                                                            user_id=getattr(enrol, "user_id", None), certificate_url=url)
        except Exception:
            logger.exception("eBook completion notification failed")


def save_progress(db: Session, tid: UUID, lesson_id: str, user: dict, token: str | None, body) -> dict:
    ctx = learner_context(db, tid, lesson_id, user, token, full_gate=True, learners_only=True)
    index = _page_index(ctx.document)
    if body.page_id not in index:
        raise _err(422, "That page is not in the current version of this eBook. Reload it")
    rec = _record(db, ctx, user, create=True)
    rec.last_page_id, rec.last_page_number, rec.last_scroll_position = body.page_id, index[body.page_id], body.last_scroll_position
    if rec.version_read is not None and rec.version_read != ctx.row.published_version:
        rec.progress_percent = 0
    rec.progress_percent = int(index[body.page_id] * 100 / len(index))
    rec.version_read = ctx.row.published_version
    rule = ctx.document.get("completion_rule", "read_to_end")
    notifications = []
    done = rule == "opened" or (rule == "read_to_end" and index[body.page_id] == len(index))
    # Repair legacy ebook completions whose separately committed course update
    # failed, using the same authorized and transactional completion path.
    course_done = _progress_out(db, ctx, rec)["lesson_completed"] if rec.completed else False
    if (done and not rec.completed) or (rec.completed and not course_done):
        try:
            _count_in_course(db, ctx, notifications)
            rec.completed = True
            rec.completed_at = rec.completed_at or _now()
        except Exception:
            db.rollback()
            raise
    db.commit()
    _send_completion_notifications(db, notifications)
    db.refresh(rec)
    return _progress_out(db, ctx, rec)


def complete_manually(db: Session, tid: UUID, lesson_id: str, user: dict, token: str | None) -> dict:
    ctx = learner_context(db, tid, lesson_id, user, token, full_gate=True, learners_only=True)
    if ctx.document.get("completion_rule") != "manual":
        raise _err(409, "This eBook is completed by reading it, not by marking it done")
    rec = _record(db, ctx, user, create=True)
    newly = not rec.completed
    course_done = _progress_out(db, ctx, rec)["lesson_completed"] if rec.completed else False
    notifications = []
    if newly or not course_done:
        try:
            _count_in_course(db, ctx, notifications)
        except Exception:
            db.rollback()
            raise
        rec.completed, rec.completed_at = True, rec.completed_at or _now()
        rec.progress_percent, rec.version_read = 100, ctx.row.published_version
    db.commit()
    _send_completion_notifications(db, notifications)
    db.refresh(rec)
    return _progress_out(db, ctx, rec)


# --- bookmarks and notes

def _page_or_422(ctx: LearnerContext, page_id: str) -> int:
    index = _page_index(ctx.document)
    if page_id not in index:
        raise _err(422, "That page is not in the current version of this eBook")
    return index[page_id]


def _with_page_state(ctx: LearnerContext, rows) -> list[dict]:
    index = _page_index(ctx.document)
    out = []
    for r in rows:
        data = {c.name: getattr(r, c.name) for c in r.__table__.columns}
        data["page_number"] = index.get(r.page_id)
        data["page_exists"] = r.page_id in index
        out.append(data)
    out.sort(key=lambda d: (d["page_number"] is None, d["page_number"] or 0, d["created_at"]))
    return out


def list_bookmarks(db: Session, tid: UUID, lesson_id: str, user: dict, token: str | None) -> dict:
    ctx = learner_context(db, tid, lesson_id, user, token, full_gate=False, learners_only=True)
    rows = db.query(TrainingEbookBookmark).filter(TrainingEbookBookmark.training_id == tid, TrainingEbookBookmark.lesson_id == lesson_id,
                                                  TrainingEbookBookmark.participant_email == ctx.email).all()
    items = _with_page_state(ctx, rows)
    return {"items": items, "total": len(items)}


def add_bookmark(db: Session, tid: UUID, lesson_id: str, user: dict, token: str | None, body) -> tuple[dict, bool]:
    ctx = learner_context(db, tid, lesson_id, user, token, full_gate=False, learners_only=True)
    if not ctx.document.get("allow_bookmarks", True):
        raise _err(403, "Bookmarks are switched off for this eBook")
    number = _page_or_422(ctx, body.page_id)
    query = db.query(TrainingEbookBookmark).filter(TrainingEbookBookmark.training_id == tid, TrainingEbookBookmark.lesson_id == lesson_id,
                                                   TrainingEbookBookmark.participant_email == ctx.email)
    same = query.filter(TrainingEbookBookmark.page_id == body.page_id, TrainingEbookBookmark.scroll_position == body.scroll_position).first()
    if same is not None:
        return _with_page_state(ctx, [same])[0], False
    if query.count() >= MAX_BOOKMARKS:
        raise _err(409, f"You can keep at most {MAX_BOOKMARKS} bookmarks in one eBook")
    row = TrainingEbookBookmark(training_id=tid, lesson_id=lesson_id, participant_email=ctx.email, page_id=body.page_id, page_number=number, label=body.label,
                                scroll_position=body.scroll_position)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _with_page_state(ctx, [row])[0], True


def delete_bookmark(db: Session, tid: UUID, lesson_id: str, bookmark_id: UUID, user: dict, token: str | None) -> None:
    ctx = learner_context(db, tid, lesson_id, user, token, full_gate=False, learners_only=True)
    row = db.query(TrainingEbookBookmark).filter(TrainingEbookBookmark.id == bookmark_id, TrainingEbookBookmark.training_id == tid,
                                                 TrainingEbookBookmark.lesson_id == lesson_id, TrainingEbookBookmark.participant_email == ctx.email).first()
    if row is None:
        raise _err(404, "Bookmark not found")
    db.delete(row)
    db.commit()


def _note_text(value: str) -> str:
    import re

    text = re.sub(r"<[^>]*>", "", value or "")
    return "".join(ch for ch in text if ch.isprintable() or ch in "\n\t").strip()


def list_notes(db: Session, tid: UUID, lesson_id: str, user: dict, token: str | None, page_id: str | None = None) -> dict:
    ctx = learner_context(db, tid, lesson_id, user, token, full_gate=False, learners_only=True)
    query = db.query(TrainingEbookNote).filter(TrainingEbookNote.training_id == tid, TrainingEbookNote.lesson_id == lesson_id, TrainingEbookNote.participant_email == ctx.email)
    if page_id:
        query = query.filter(TrainingEbookNote.page_id == page_id)
    items = _with_page_state(ctx, query.all())
    return {"items": items, "total": len(items)}


def add_note(db: Session, tid: UUID, lesson_id: str, user: dict, token: str | None, body) -> dict:
    ctx = learner_context(db, tid, lesson_id, user, token, full_gate=False, learners_only=True)
    if not ctx.document.get("allow_notes", True):
        raise _err(403, "Notes are switched off for this eBook")
    number = _page_or_422(ctx, body.page_id)
    text = _note_text(body.text)
    if not text:
        raise _err(422, "The note is empty")
    count = db.query(func.count(TrainingEbookNote.id)).filter(TrainingEbookNote.training_id == tid, TrainingEbookNote.lesson_id == lesson_id,
                                                              TrainingEbookNote.participant_email == ctx.email).scalar()
    if count >= MAX_NOTES:
        raise _err(409, f"You can keep at most {MAX_NOTES} notes in one eBook")
    row = TrainingEbookNote(training_id=tid, lesson_id=lesson_id, participant_email=ctx.email, page_id=body.page_id, page_number=number, text=text,
                            quote=_note_text(body.quote or "")[:1000] or None, color=body.color, scroll_position=body.scroll_position)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _with_page_state(ctx, [row])[0]


def _own_note(db: Session, ctx: LearnerContext, tid: UUID, lesson_id: str, note_id: UUID) -> TrainingEbookNote:
    row = db.query(TrainingEbookNote).filter(TrainingEbookNote.id == note_id, TrainingEbookNote.training_id == tid, TrainingEbookNote.lesson_id == lesson_id,
                                             TrainingEbookNote.participant_email == ctx.email).first()
    if row is None:
        raise _err(404, "Note not found")
    return row


def update_note(db: Session, tid: UUID, lesson_id: str, note_id: UUID, user: dict, token: str | None, body) -> dict:
    ctx = learner_context(db, tid, lesson_id, user, token, full_gate=False, learners_only=True)
    row = _own_note(db, ctx, tid, lesson_id, note_id)
    data = body.model_dump(exclude_unset=True)
    if "text" in data:
        text = _note_text(data["text"] or "")
        if not text:
            raise _err(422, "The note is empty")
        row.text = text
    if "color" in data:
        row.color = data["color"]
    db.commit()
    db.refresh(row)
    return _with_page_state(ctx, [row])[0]


def delete_note(db: Session, tid: UUID, lesson_id: str, note_id: UUID, user: dict, token: str | None) -> None:
    ctx = learner_context(db, tid, lesson_id, user, token, full_gate=False, learners_only=True)
    row = _own_note(db, ctx, tid, lesson_id, note_id)
    db.delete(row)
    db.commit()


def copy_for_duplicate(db: Session, source_training_id: UUID, new_training) -> int:
    """A copy of a training keeps its eBooks' pages as drafts. Nothing is live for the copy and nothing is approved."""
    count = 0
    for row in db.query(TrainingEbook).filter(TrainingEbook.training_id == source_training_id).all():
        db.add(TrainingEbook(training_id=new_training.id, tenant_id=new_training.tenant_id or row.tenant_id, section_id=row.section_id, lesson_id=row.lesson_id,
                             draft=deepcopy(row.draft), draft_version=1, status="draft", created_by=row.created_by, updated_by=row.updated_by,
                             updated_by_name=row.updated_by_name))
        count += 1
    return count
