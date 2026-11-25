"""Product media upload: photos and videos for the Product create / edit screens (docs/product-media-upload.md).

    POST /api/v1/products/upload            multipart `file` -> { url, name, size, type, kind, stored_name }
    GET  /api/v1/products/upload/{stored}   the file, inline, with HTTP Range support so a video can play and seek
    GET  /api/v1/products/upload/policy     the limits, for the uploader

Images: JPEG, PNG, WebP, GIF, at most PRODUCT_MAX_IMAGE_MB (10). Videos: MP4, WebM, QuickTime (.mov), at most PRODUCT_MAX_VIDEO_MB (50).
The declared type, the file extension and the file's real first bytes must all agree (no SVG, no scripts renamed to .jpg).
Files live in UPLOAD_DIR/products/<random>.<ext>. Nothing is stored in the database: a Product keeps the returned `url` in its image field. A file nobody saved into
a Product is deleted after PRODUCT_MEDIA_ORPHAN_DAYS (7) the next time someone uploads.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.core.config import settings
from app.services.event_media_service import MIME_EXTS, MIME_SIGNATURE, _sniff
from app.utils.public_urls import public_media_base, to_https

logger = logging.getLogger(__name__)

MB = 1024 * 1024
CHUNK = MB
IMAGE_MIMES = ("image/jpeg", "image/png", "image/webp", "image/gif")
VIDEO_MIMES = ("video/mp4", "video/webm", "video/quicktime")
EXT_TO_MIME = {ext: mime for mime in IMAGE_MIMES + VIDEO_MIMES for ext in MIME_EXTS[mime]}
STORED_NAME = re.compile(r"^[0-9a-f]{32}\.(" + "|".join(sorted(EXT_TO_MIME, key=len, reverse=True)) + r")$")
GENERIC_TYPES = ("", "application/octet-stream", "binary/octet-stream")


def limits() -> dict:
    return {
        "image": {"mime_types": list(IMAGE_MIMES), "extensions": sorted(e for m in IMAGE_MIMES for e in MIME_EXTS[m]), "max_size_bytes": settings.PRODUCT_MAX_IMAGE_MB * MB},
        "video": {"mime_types": list(VIDEO_MIMES), "extensions": sorted(e for m in VIDEO_MIMES for e in MIME_EXTS[m]), "max_size_bytes": settings.PRODUCT_MAX_VIDEO_MB * MB},
    }


def media_root() -> Path:
    from app.services.attachment_storage import ensure_upload_directory

    root = ensure_upload_directory() / "products"
    root.mkdir(parents=True, exist_ok=True)
    return root


def public_url(stored_name: str, request=None) -> str:
    """The absolute https URL of a stored file: the configured public base, else the address the request came in on."""
    base = public_media_base() or (str(request.base_url).rstrip("/") if request is not None else "")
    return to_https(f"{base}/api/v1/products/upload/{stored_name}")


def _kind_and_mime(upload: UploadFile) -> tuple[str, str, str, str]:
    """(kind, mime, extension, original name) from what the client says, or the error to send."""
    original = Path(upload.filename or "upload").name[:255] or "upload"
    ext = Path(original).suffix.lstrip(".").lower()
    declared = (upload.content_type or "").split(";")[0].strip().lower()
    if declared in GENERIC_TYPES:
        declared = EXT_TO_MIME.get(ext, "")            # a browser that sent no type: the extension says it, the contents are checked below
    if declared not in IMAGE_MIMES + VIDEO_MIMES:
        raise HTTPException(415, f"Unsupported file type '{declared or 'unknown'}'. Photos: JPEG, PNG, WebP, GIF. Videos: MP4, WebM, MOV")
    if ext not in MIME_EXTS[declared]:
        raise HTTPException(415, f"The file extension '.{ext}' does not match {declared} (expected {list(MIME_EXTS[declared])})")
    return ("image" if declared in IMAGE_MIMES else "video"), declared, ext, original


def store(upload: UploadFile, request=None) -> dict:
    kind, mime, ext, original = _kind_and_mime(upload)
    cap = (settings.PRODUCT_MAX_IMAGE_MB if kind == "image" else settings.PRODUCT_MAX_VIDEO_MB) * MB
    stored = f"{uuid.uuid4().hex}.{ext}"
    target = media_root() / stored
    size, head = 0, b""
    try:
        with open(target, "wb") as out:
            while True:
                chunk = upload.file.read(CHUNK)
                if not chunk:
                    break
                if size == 0:
                    head = chunk[:16]
                size += len(chunk)
                if size > cap:
                    raise HTTPException(413, f"{'Photos' if kind == 'image' else 'Videos'} may be at most {cap // MB} MB")
                out.write(chunk)
        if size == 0:
            raise HTTPException(400, "Uploaded file is empty")
        if _sniff(head) != MIME_SIGNATURE[mime]:
            raise HTTPException(415, f"The file's contents are not a real {mime}")
    except HTTPException:
        target.unlink(missing_ok=True)
        raise
    except OSError as exc:
        target.unlink(missing_ok=True)
        logger.exception("product media could not be stored")
        raise HTTPException(500, "Failed to store the file") from exc
    return {"url": public_url(stored, request), "name": original, "size": size, "type": mime, "kind": kind, "stored_name": stored}


def path_for(stored_name: str) -> tuple[Path, str]:
    """The file and its media type for a stored name, or 404 (any name that is not exactly what an upload produced is a 404)."""
    if not STORED_NAME.match(stored_name or ""):
        raise HTTPException(404, "File not found")
    path = media_root() / stored_name
    if not path.is_file():
        raise HTTPException(404, "File not found")
    return path, EXT_TO_MIME[stored_name.rsplit(".", 1)[1]]


def purge_unreferenced(db, now: float | None = None, limit: int = 50) -> int:
    """Delete uploads older than PRODUCT_MEDIA_ORPHAN_DAYS that nothing refers to: a product's images, a service's banner (inline images are stored here too)
    or an attribute's photos and videos. Best effort, bounded per call."""
    from sqlalchemy import String, cast

    from app.models.attribute_model import DynamicAttribute
    from app.models.product_model import Product
    from app.models.service_model import Service

    def in_use(name: str) -> bool:
        return (db.query(Product.id).filter(Product.product_images.contains(name)).first() is not None
                or db.query(Service.id).filter(Service.banner_image.contains(name)).first() is not None
                or db.query(DynamicAttribute.id).filter(cast(DynamicAttribute.media, String).contains(name)).first() is not None)

    cutoff = (now or time.time()) - settings.PRODUCT_MEDIA_ORPHAN_DAYS * 86400
    removed = checked = 0
    for path in media_root().iterdir():
        if checked >= limit:
            break
        try:
            if not path.is_file() or path.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        checked += 1
        if not in_use(path.name):
            path.unlink(missing_ok=True)
            removed += 1
    return removed
