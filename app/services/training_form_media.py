"""Validate configured media against stored bytes, not client metadata."""
from __future__ import annotations

from io import BytesIO
from urllib.parse import urlsplit
from zipfile import ZipFile, BadZipFile

from fastapi import HTTPException

SUPPORTED_MIMES = {
    **{m: "image" for m in ("image/jpeg", "image/png", "image/gif", "image/webp")},
    **{m: "video" for m in ("video/mp4", "video/quicktime", "video/webm", "video/ogg", "video/x-msvideo")},
    **{m: "document" for m in ("application/pdf", "text/plain",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
}


def detect_mime(data):
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data[:4] == b"\x1aE\xdf\xa3" and b"webm" in data[:4096]:
        return "video/webm"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "video/quicktime" if data[8:12] == b"qt  " else "video/mp4"
    if data.startswith(b"OggS") and b"theora" in data[:4096]:
        return "video/ogg"
    if data[:4] == b"RIFF" and data[8:12] == b"AVI ":
        return "video/x-msvideo"
    try:
        from PIL import Image
        with Image.open(BytesIO(data)) as img:
            mime = Image.MIME.get(img.format)
            img.verify()
            if mime in SUPPORTED_MIMES:
                return mime
    except (OSError, ValueError, SyntaxError):
        pass
    if data.startswith(b"PK"):
        try:
            with ZipFile(BytesIO(data)) as archive:
                names = set(archive.namelist())
                if "[Content_Types].xml" in names:
                    for member, mime in (("word/document.xml", "wordprocessingml.document"),
                                         ("xl/workbook.xml", "spreadsheetml.sheet"),
                                         ("ppt/presentation.xml", "presentationml.presentation")):
                        if member in names:
                            return "application/vnd.openxmlformats-officedocument." + mime
        except BadZipFile:
            pass
        return None
    try:
        text = data.decode("utf-8")
        if text and all(c.isprintable() or c in "\r\n\t" for c in text):
            return "text/plain"
    except UnicodeDecodeError:
        pass
    return None


def policy_cap(field):
    from app.services.training_form_rules import settings, media_kind
    from app.services.training_upload_service import MAX_SIZE_BYTES
    purpose = {"image": "image", "video": "lesson_video", "document": "lesson_document"}[media_kind(field)]
    cap = MAX_SIZE_BYTES[purpose]
    configured = (settings(field).get("upload") or {}).get("max_file_size_mb")
    return min(cap, int(configured * 1024 * 1024)) if configured is not None else cap


def validate_media_bytes(field, data, claimed_mime=None, purpose=None):
    from app.services.training_form_rules import settings, media_kind
    kind = media_kind(field)
    allowed_purposes = {"image": {"image"}, "video": {"lesson_video"}, "document": {"lesson_document", "lesson_pdf"}}
    if purpose and purpose not in allowed_purposes[kind]:
        raise HTTPException(400, "Upload purpose does not match configured field type")
    if len(data) > policy_cap(field):
        raise HTTPException(413, f"File exceeds configured maximum of {policy_cap(field)} bytes")
    detected = detect_mime(data)
    allowed = (settings(field).get("upload") or {}).get("allowed_mime_types")
    if SUPPORTED_MIMES.get(detected) != kind or (allowed is not None and detected not in allowed):
        raise HTTPException(415, "File format is not allowed by the configured field upload policy")
    if claimed_mime and claimed_mime.split(";", 1)[0].strip().lower() != detected:
        raise HTTPException(415, "Declared MIME type does not match file content")
    if purpose == "lesson_pdf" and detected != "application/pdf":
        raise HTTPException(415, "lesson_pdf requires a PDF file")
    return detected


def validate_media_value(field, value):
    """Prevents bypass by uploading through generic endpoints or supplying fake size/type."""
    from app.services.training_form_rules import empty, settings
    if settings(field).get("upload") is None or empty(value):
        return
    from app.core.config import settings as config
    from app.services.training_upload_service import resolve_training_upload
    from app.services.generic_upload_service import resolve_generic_upload_path
    for item in value if isinstance(value, list) else [value]:
        url = item.get("url") if isinstance(item, dict) else item
        if not isinstance(url, str):
            raise HTTPException(400, "Configured media requires an uploaded file URL")
        parsed = urlsplit(url)
        base = urlsplit(config.PUBLIC_API_BASE_URL or "")
        if parsed.query or parsed.fragment or (parsed.netloc and (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc)):
            raise HTTPException(400, "Configured media requires a local uploaded file URL")
        if parsed.path.startswith("/api/v1/trainings/upload/"):
            path = resolve_training_upload(parsed.path[len("/api/v1/trainings/upload/"):])
        elif parsed.path.startswith("/api/v1/uploads/") and len(parsed.path.split("/")) == 6:
            path = resolve_generic_upload_path(*parsed.path.split("/")[-2:])
        else:
            raise HTTPException(400, "Configured media requires a local uploaded file URL")
        if path.stat().st_size > policy_cap(field):
            raise HTTPException(413, "Stored file exceeds configured maximum size")
        validate_media_bytes(field, path.read_bytes())


def resolve_upload_field(db, user, field_key=None, training_id=None):
    from app.services.training_form_config_service import get_active_form_configuration_service, get_training_form_configuration_service
    from app.services.training_form_rules import field_index, fields, settings, media_kind
    if training_id:
        from app.repository.training_repo import require_training_owner
        require_training_owner(db, training_id, user)
        resolved = get_training_form_configuration_service(db, training_id, user)
    else:
        try:
            resolved = get_active_form_configuration_service(db, user)
        except HTTPException as exc:
            if exc.status_code == 404 and not field_key:
                return None
            raise
    sections = resolved["sections"]
    if not field_key:
        if any(settings(f).get("upload") is not None for f in fields(sections)):
            raise HTTPException(400, "field_key is required for configured Training uploads")
        return None
    field = field_index(sections).get(field_key)
    enabled_ids = {f["id"] for s in sections if s.get("is_enabled", True) for f in s.get("fields", []) if f.get("is_enabled", True)}
    if not field or field["id"] not in enabled_ids or not media_kind(field):
        raise HTTPException(400, "field_key must identify an enabled media field in the resolved form")
    return field
