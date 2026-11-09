"""Files for blog and news posts: a cover image, more images, and supporting documents.

Images: JPEG, PNG, WebP or GIF, decoded and checked (media_service.inspect_image), at most BLOG_MAX_IMAGE_MB.
Videos: MP4, MOV or WebM, read from the content (the file header, not the name), at most BLOG_MAX_VIDEO_MB. They are stored as they are: no transcoding and no thumbnail.
Documents: PDF, Word (doc, docx), Excel (xls, xlsx), PowerPoint (ppt, pptx), plain text and CSV, at most BLOG_MAX_DOCUMENT_MB. The type is
read from the content, not trusted from the name, and must agree with the extension.
Every file goes through the virus scan when one is set up. A file is public only while a live post uses it; before that it can be
fetched only by the business that uploaded it and by Super Admins.
"""
import hashlib
import hmac
import io
import re
import time
import zipfile
from datetime import datetime
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

PURPOSES = ("cover_image", "image", "document", "video")
VIDEO_TYPES = {"mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm"}
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
    return {"image_max_mb": settings.BLOG_MAX_IMAGE_MB, "document_max_mb": settings.BLOG_MAX_DOCUMENT_MB, "video_max_mb": settings.BLOG_MAX_VIDEO_MB,
            "video_extensions": sorted(VIDEO_TYPES), "signed_url_seconds": settings.BLOG_SIGNED_URL_SECONDS,
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


def _video_type(data: bytes, name: str) -> tuple[str, str]:
    ext = Path(name or "").suffix.lstrip(".").lower()
    if ext not in VIDEO_TYPES:
        raise problem(415, f"Videos can be {', '.join(sorted(VIDEO_TYPES))} files", "UNSUPPORTED_TYPE", allowed=sorted(VIDEO_TYPES))
    if ext in ("mp4", "mov"):
        ok = len(data) > 12 and data[4:8] == b"ftyp" and (data[8:12] == b"qt  ") == (ext == "mov")
    else:
        ok = data[:4] == b"\x1a\x45\xdf\xa3" and b"webm" in data[:64]
    if not ok:
        raise problem(415, f"This file is not a valid .{ext} video", "UNSUPPORTED_TYPE", allowed=sorted(VIDEO_TYPES))
    return ext, VIDEO_TYPES[ext]


def _check_declared(upload: UploadFile, family: str) -> None:
    """A browser says what it thinks it is sending. If that is a different kind of file from what the content proves, refuse: it is a sign of a mix-up or a disguise."""
    declared = (upload.content_type or "").split(";")[0].strip().lower()
    if declared and declared != "application/octet-stream" and not declared.startswith(family + "/"):
        raise problem(415, f"The file says it is {declared} but this upload takes {family} files", "MIME_MISMATCH", declared=declared)


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


def _inspect(upload: UploadFile, purpose: str) -> dict:
    """Reads and checks an upload for its purpose. Returns what is needed to store it."""
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
    elif purpose == "video":
        _check_declared(upload, "video")
        data = _read(upload, settings.BLOG_MAX_VIDEO_MB)
        ext, content_type = _video_type(data, name)
        try:
            scan = virus_scan.scan_or_refuse(data)
        except HTTPException as exc:
            raise _as_problem(exc)
        digest = hashlib.sha256(data).hexdigest()
    else:
        _check_declared(upload, "image")
        data = _read(upload, settings.BLOG_MAX_IMAGE_MB)
        try:
            info = media_service.inspect_image(data, name)
        except HTTPException as exc:
            raise _as_problem(exc)
        ext = next(e for e in sorted(media_service.FORMATS[_format(info.mime)][1]))
        content_type, width, height, scan, digest = info.mime, info.width, info.height, info.scan_status, info.sha256
    return {"data": data, "ext": ext, "name": name, "content_type": content_type, "width": width, "height": height, "scan": scan, "digest": digest}


def _write(info: dict) -> str:
    folder = settings.upload_dir_path / "blogs"
    folder.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid4().hex}.{info['ext']}"
    (folder / stored).write_bytes(info["data"])
    return stored


def store(db: Session, actor: BlogActor, upload: UploadFile, purpose: str) -> dict:
    if purpose not in PURPOSES:
        raise problem(422, f"purpose must be one of: {', '.join(PURPOSES)}", "VALIDATION_FAILED", errors=[
            {"field": "purpose", "code": "INVALID", "message": f"purpose must be one of: {', '.join(PURPOSES)}"}])
    info = _inspect(upload, purpose)
    row = BlogFile(tenant_id=actor.tenant_id, uploaded_by=actor.id, purpose=purpose, stored_name=_write(info), original_name=info["name"][:255] or None,
                   content_type=info["content_type"], size=len(info["data"]), width=info["width"], height=info["height"], sha256=info["digest"], scan_status=info["scan"])
    db.add(row)
    db.commit()
    db.refresh(row)
    return summary(row)


def _format(mime: str) -> str:
    return {v[0]: k for k, v in media_service.FORMATS.items()}[mime]


# ---------------------------------------------------------------- reading a file back

def get_row(db: Session, file_id: UUID) -> BlogFile | None:
    row = db.get(BlogFile, file_id)
    return None if row is None or row.is_deleted else row


def _needle(file_id) -> str:
    return f"%{file_id}%"


def _references(query, file_id):
    n = _needle(file_id)
    return query.filter(or_(BlogPost.cover_image_url.like(n), BlogPost.content.like(n), cast(BlogPost.additional_images, String).like(n), cast(BlogPost.supporting_documents, String).like(n),
                            cast(BlogPost.custom_values, String).like(n), cast(BlogPost.pending_revision, String).like(n)))


def is_used_by_live_post(db: Session, file_id: UUID) -> bool:
    """A live post's own content uses the file (a waiting edit of a live post does not count: it is not public yet)."""
    n = _needle(file_id)
    return db.query(BlogPost.id).filter(
        BlogPost.is_live.is_(True), BlogPost.is_deleted.is_(False),
        or_(BlogPost.cover_image_url.like(n), BlogPost.content.like(n), cast(BlogPost.additional_images, String).like(n),
            cast(BlogPost.supporting_documents, String).like(n), cast(BlogPost.custom_values, String).like(n)),
    ).first() is not None


def used_by(db: Session, file_id: UUID) -> list[dict]:
    """Every post that still uses the file, in any state, including a waiting edit."""
    rows = _references(db.query(BlogPost).filter(BlogPost.is_deleted.is_(False)), file_id).all()
    return [{"post_id": r.id, "title": r.title, "status": r.status, "is_live": bool(r.is_live)} for r in rows]


def _signature(file_id, expires: int) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f"blog-file:{file_id}:{expires}".encode(), hashlib.sha256).hexdigest()[:40]


def signed_url(db: Session, actor: BlogActor, file_id: UUID) -> dict:
    """A link that works without signing in, until it expires: for an app or a page that cannot send the token. Anyone holding it can read the file."""
    row = _mine(db, actor, file_id)
    expires = int(time.time() + settings.BLOG_SIGNED_URL_SECONDS)
    return {"url": f"{file_url(row)}?expires={expires}&signature={_signature(row.id, expires)}", "expires_at": datetime.utcfromtimestamp(expires)}


def _valid_signature(file_id, expires: str | None, signature: str | None) -> bool:
    try:
        stamp = int(expires or "")
    except ValueError:
        return False
    return bool(signature) and stamp > time.time() and hmac.compare_digest(signature, _signature(file_id, stamp))


def open_file(db: Session, file_id: UUID, actor: BlogActor | None, *, expires: str | None = None, signature: str | None = None) -> tuple[Path, BlogFile]:
    row = get_row(db, file_id)
    allowed = False
    if row is not None:
        if actor is not None and (actor.role == "super_admin" or (actor.tenant_id is not None and actor.tenant_id == row.tenant_id)):
            allowed = True
        elif _valid_signature(file_id, expires, signature):
            allowed = True
        else:
            allowed = is_used_by_live_post(db, file_id)
    if not allowed:
        raise problem(404, "File not found", "FILE_NOT_FOUND")
    path = settings.upload_dir_path / "blogs" / row.stored_name
    if not path.is_file():
        raise problem(404, "File not found", "FILE_NOT_FOUND")
    return path, row


def _mine(db: Session, actor: BlogActor, file_id: UUID) -> BlogFile:
    """A file the caller may manage: their business's own, or any for a Super Admin. Anyone else's looks like a file that does not exist."""
    row = get_row(db, file_id)
    if row is None or not (actor.role == "super_admin" or (actor.tenant_id is not None and row.tenant_id == actor.tenant_id)):
        raise problem(404, "File not found", "FILE_NOT_FOUND")
    return row


def detail(db: Session, actor: BlogActor, file_id: UUID) -> dict:
    row = _mine(db, actor, file_id)
    return {**summary(row), "sha256": row.sha256, "replaced_at": row.replaced_at, "used_by": used_by(db, row.id)}


def list_files(db: Session, actor: BlogActor, *, purpose: str | None, page: int, page_size: int) -> tuple[list[dict], int]:
    query = db.query(BlogFile).filter(BlogFile.is_deleted.is_(False))
    if actor.role != "super_admin":
        query = query.filter(BlogFile.tenant_id == actor.tenant_id)
    if purpose:
        query = query.filter(BlogFile.purpose == purpose)
    total = query.count()
    rows = query.order_by(BlogFile.created_at.desc(), BlogFile.id).offset((page - 1) * page_size).limit(page_size).all()
    return [summary(r) for r in rows], total


def _unlink(row: BlogFile) -> None:
    try:
        (settings.upload_dir_path / "blogs" / row.stored_name).unlink(missing_ok=True)
    except OSError:
        pass


def delete_file(db: Session, actor: BlogActor, file_id: UUID) -> None:
    """Only a file no post uses. One that is used is 409 FILE_IN_USE with the posts, so the author can take it out of them first."""
    row = _mine(db, actor, file_id)
    users = used_by(db, row.id)
    if users:
        raise problem(409, "This file is still used by a post. Take it out of the post first", "FILE_IN_USE", used_by=[{**u, "post_id": str(u["post_id"])} for u in users])
    _unlink(row)
    row.is_deleted, row.deleted_at = True, datetime.utcnow()
    db.commit()


def replace_file(db: Session, actor: BlogActor, file_id: UUID, upload: UploadFile) -> dict:
    """New content under the same file id and the same address, checked like a new upload of the same purpose. Refused while a live post uses the file, because that would change a
    published post without the Super Admin's approval: upload a new file and edit the post instead. What a draft saved about the file (its name, size and type) is refreshed the next time the post is saved."""
    row = _mine(db, actor, file_id)
    if is_used_by_live_post(db, row.id):
        raise problem(409, "A published post uses this file, so it cannot be replaced. Upload a new file and edit the post: the edit goes through approval", "FILE_IN_USE_LIVE")
    info = _inspect(upload, row.purpose)
    previous = row.stored_name
    row.stored_name, row.original_name, row.content_type = _write(info), info["name"][:255] or None, info["content_type"]
    row.size, row.width, row.height, row.sha256, row.scan_status, row.replaced_at = len(info["data"]), info["width"], info["height"], info["digest"], info["scan"], datetime.utcnow()
    db.commit()
    try:
        (settings.upload_dir_path / "blogs" / previous).unlink(missing_ok=True)
    except OSError:
        pass
    db.refresh(row)
    return summary(row)


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
