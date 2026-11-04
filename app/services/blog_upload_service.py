"""Files for blog and news posts: a cover image, more images, and supporting documents.

Images: JPEG, PNG, WebP or GIF, decoded and checked (media_service.inspect_image), at most BLOG_MAX_IMAGE_MB.
Documents: PDF, Word (doc, docx), Excel (xls, xlsx), PowerPoint (ppt, pptx), plain text and CSV, at most BLOG_MAX_DOCUMENT_MB. The type is
read from the content, not trusted from the name, and must agree with the extension.
Every file goes through the virus scan when one is set up. A file is public only while a live post uses it; before that it can be
fetched only by the business that uploaded it and by Super Admins.
"""
import hashlib
import io
import re
import zipfile
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile
from sqlalchemy import String, cast, or_
from sqlalchemy.orm import Session

from app.core.blog_access import BlogActor
from app.core.config import settings
from app.exceptions.problem import problem
from app.models.blog_model import BlogFile, BlogPost
from app.services import media_service, virus_scan

PURPOSES = ("cover_image", "image", "document")
DOCUMENT_TYPES = {
    "pdf": "application/pdf", "doc": "application/msword", "xls": "application/vnd.ms-excel", "ppt": "application/vnd.ms-powerpoint",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "txt": "text/plain", "csv": "text/csv",
}
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
OOXML_FOLDER = {"docx": "word/", "xlsx": "xl/", "pptx": "ppt/"}
CHUNK = 1024 * 1024


def limits() -> dict:
    return {"image_max_mb": settings.BLOG_MAX_IMAGE_MB, "document_max_mb": settings.BLOG_MAX_DOCUMENT_MB,
            "image_types": ["image/jpeg", "image/png", "image/webp", "image/gif"], "document_extensions": sorted(DOCUMENT_TYPES),
            "max_additional_images": settings.BLOG_MAX_ADDITIONAL_IMAGES, "max_documents": settings.BLOG_MAX_DOCUMENTS,
            "max_reference_urls": settings.BLOG_MAX_REFERENCE_URLS}


def _read(upload: UploadFile, limit_mb: int) -> bytes:
    limit = limit_mb * 1024 * 1024
    data = bytearray()
    while True:
        chunk = upload.file.read(CHUNK)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > limit:
            raise problem(413, f"The file is larger than {limit_mb} MB", "FILE_TOO_LARGE", max_mb=limit_mb)
    if not data:
        raise problem(422, "The file is empty", "EMPTY_FILE")
    return bytes(data)


def _document_type(data: bytes, name: str) -> tuple[str, str]:
    ext = Path(name or "").suffix.lstrip(".").lower()
    if ext not in DOCUMENT_TYPES:
        raise problem(415, f"Documents can be {', '.join(sorted(DOCUMENT_TYPES))} files", "UNSUPPORTED_TYPE", allowed=sorted(DOCUMENT_TYPES))
    ok = False
    if ext == "pdf":
        ok = data[:5] == b"%PDF-"
    elif ext in ("doc", "xls", "ppt"):
        ok = data[:8] == OLE
    elif ext in OOXML_FOLDER:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
            ok = "[Content_Types].xml" in names and any(n.startswith(OOXML_FOLDER[ext]) for n in names)
        except zipfile.BadZipFile:
            ok = False
    else:
        try:
            data.decode("utf-8")
            ok = b"\x00" not in data
        except UnicodeDecodeError:
            ok = False
    if not ok:
        raise problem(415, f"This file is not a valid .{ext} document", "UNSUPPORTED_TYPE", allowed=sorted(DOCUMENT_TYPES))
    return ext, DOCUMENT_TYPES[ext]


def _as_problem(exc: HTTPException) -> Exception:
    text = str(exc.detail)
    if exc.status_code == 415:
        return problem(415, text, "UNSUPPORTED_TYPE")
    if exc.status_code == 503:
        return problem(503, text, "SCANNER_UNAVAILABLE")
    if "virus" in text.lower():
        return problem(422, text, "VIRUS_DETECTED")
    return problem(exc.status_code, text, "INVALID_FILE")


def file_url(file: BlogFile) -> str:
    from app.utils.public_urls import public_media_base, to_https

    return to_https(f"{public_media_base()}/api/v1/cms/blogs/files/{file.id}")


def summary(file: BlogFile) -> dict:
    return {"file_id": file.id, "url": file_url(file), "name": file.original_name, "size": file.size, "content_type": file.content_type,
            "purpose": file.purpose, "width": file.width, "height": file.height, "scan_status": file.scan_status, "created_at": file.created_at}


def store(db: Session, actor: BlogActor, upload: UploadFile, purpose: str) -> dict:
    if purpose not in PURPOSES:
        raise problem(422, f"purpose must be one of: {', '.join(PURPOSES)}", "VALIDATION_FAILED", errors=[
            {"field": "purpose", "code": "INVALID", "message": f"purpose must be one of: {', '.join(PURPOSES)}"}])
    name = (upload.filename or "").strip()
    width = height = None
    if purpose == "document":
        data = _read(upload, settings.BLOG_MAX_DOCUMENT_MB)
        ext, content_type = _document_type(data, name)
        try:
            scan = virus_scan.scan_or_refuse(data)
        except HTTPException as exc:
            raise _as_problem(exc)
        digest = hashlib.sha256(data).hexdigest()
    else:
        data = _read(upload, settings.BLOG_MAX_IMAGE_MB)
        try:
            info = media_service.inspect_image(data, name)
        except HTTPException as exc:
            raise _as_problem(exc)
        ext = next(e for e in sorted(media_service.FORMATS[_format(info.mime)][1]))
        content_type, width, height, scan, digest = info.mime, info.width, info.height, info.scan_status, info.sha256
    folder = settings.upload_dir_path / "blogs"
    folder.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid4().hex}.{ext}"
    (folder / stored).write_bytes(data)
    row = BlogFile(tenant_id=actor.tenant_id, uploaded_by=actor.id, purpose=purpose, stored_name=stored, original_name=name[:255] or None,
                   content_type=content_type, size=len(data), width=width, height=height, sha256=digest, scan_status=scan)
    db.add(row)
    db.commit()
    db.refresh(row)
    return summary(row)


def _format(mime: str) -> str:
    return {v[0]: k for k, v in media_service.FORMATS.items()}[mime]


# ---------------------------------------------------------------- reading a file back

def get_row(db: Session, file_id: UUID) -> BlogFile | None:
    return db.get(BlogFile, file_id)


def is_used_by_live_post(db: Session, file_id: UUID) -> bool:
    needle = f"%{file_id}%"
    return db.query(BlogPost.id).filter(
        BlogPost.is_live.is_(True), BlogPost.is_deleted.is_(False),
        or_(BlogPost.cover_image_url.like(needle), BlogPost.content.like(needle), cast(BlogPost.additional_images, String).like(needle),
            cast(BlogPost.supporting_documents, String).like(needle), cast(BlogPost.custom_values, String).like(needle)),
    ).first() is not None


def open_file(db: Session, file_id: UUID, actor: BlogActor | None) -> tuple[Path, BlogFile]:
    row = get_row(db, file_id)
    allowed = False
    if row is not None:
        if actor is not None and (actor.role == "super_admin" or (actor.tenant_id is not None and actor.tenant_id == row.tenant_id)):
            allowed = True
        else:
            allowed = is_used_by_live_post(db, file_id)
    if not allowed:
        raise problem(404, "File not found", "FILE_NOT_FOUND")
    path = settings.upload_dir_path / "blogs" / row.stored_name
    if not path.is_file():
        raise problem(404, "File not found", "FILE_NOT_FOUND")
    return path, row


def owned_files(db: Session, urls_or_ids: list[str], actor: BlogActor) -> dict[str, BlogFile]:
    """The uploads among these addresses that the caller may attach to a post (own business's, or any for a Super Admin)."""
    out: dict[str, BlogFile] = {}
    for url in urls_or_ids:
        match = re.search(r"/cms/blogs/files/([0-9a-fA-F-]{36})", url or "")
        if not match:
            continue
        row = get_row(db, UUID(match.group(1)))
        if row is not None and (actor.role == "super_admin" or (actor.tenant_id is not None and row.tenant_id == actor.tenant_id)):
            out[url] = row
    return out
