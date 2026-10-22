"""Image assets: checking an uploaded picture, recording it, who may fetch it, and when it may be deleted.

What is checked for an uploaded image (before anything is stored):
  - it really is a JPEG, PNG, WebP or GIF picture (decoded, not trusted from the file name or the declared type), and its type matches
    the file extension
  - its longest side is at most MAX_IMAGE_DIMENSION pixels and its total pixels at most MAX_IMAGE_PIXELS (so a tiny file cannot unpack
    into a huge bitmap)
  - its size is at most MAX_IMAGE_SIZE_MB (checked by the upload service)
  - the virus scanner, when one is set up, finds nothing
SVG is not accepted: an SVG file can carry scripts, and the mobile apps and the editor display images by address.

An asset belongs to the business (tenant) of the person who uploaded it. Its file is served only to that business's staff, to Super
Admins, and to learners enrolled in a training whose *published* eBook uses it. A picture that an eBook (draft or published) still uses
cannot be deleted.
"""
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.ebook_model import MediaAsset, TrainingEbook
from app.services import virus_scan

FORMATS = {"JPEG": ("image/jpeg", {"jpg", "jpeg"}), "PNG": ("image/png", {"png"}), "WEBP": ("image/webp", {"webp"}), "GIF": ("image/gif", {"gif"})}


@dataclass
class ImageInfo:
    width: int
    height: int
    mime: str
    sha256: str
    scan_status: str


def _err(status: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail=detail)


def inspect_image(data: bytes, filename: str | None = None) -> ImageInfo:
    """Decode the picture and enforce the limits. Raises the HTTP error to send back."""
    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(io.BytesIO(data))
        width, height = image.size
        fmt = (image.format or "").upper()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise _err(415, "This file is not a valid image. Use a JPEG, PNG or WebP picture")
    if fmt not in FORMATS:
        raise _err(415, f"{fmt.title() or 'This'} images are not accepted. Use a JPEG, PNG or WebP picture")
    ext = (Path(filename or "").suffix or "").lstrip(".").lower()
    if ext and ext not in FORMATS[fmt][1]:
        raise _err(415, f"The file is a {fmt} picture but its name ends in .{ext}")
    if width <= 0 or height <= 0:
        raise _err(415, "This image has no size")
    if max(width, height) > settings.MAX_IMAGE_DIMENSION:
        raise _err(422, f"The image is {width} x {height} pixels. The longest side can be at most {settings.MAX_IMAGE_DIMENSION} pixels")
    if width * height > settings.MAX_IMAGE_PIXELS:
        raise _err(422, f"The image has too many pixels ({width * height:,}). The most allowed is {settings.MAX_IMAGE_PIXELS:,}")
    try:
        image.verify()                     # reads the whole file and fails on a truncated or corrupt one
    except Exception:
        raise _err(415, "This image file is damaged")
    return ImageInfo(width=width, height=height, mime=FORMATS[fmt][0], sha256=hashlib.sha256(data).hexdigest(), scan_status=virus_scan.scan_or_refuse(data))


def user_tenant(db: Session, current_user: dict, access_token: str | None = None) -> UUID | None:
    from app.core.auth_context import resolve_auth_tenant_id_with_db

    try:
        raw = resolve_auth_tenant_id_with_db(db, current_user, access_token=access_token)
        return UUID(str(raw)) if raw else None
    except Exception:
        return None


def file_url(asset: MediaAsset) -> str:
    from app.utils.public_urls import public_media_base, to_https

    return to_https(f"{public_media_base()}/api/v1/media/{asset.id}/file")


def register_image(db: Session, current_user: dict, info: ImageInfo, stored_name: str, filename: str | None, size: int) -> MediaAsset:
    try:
        owner = UUID(str(current_user.get("id")))
    except (ValueError, TypeError):
        owner = None
    asset = MediaAsset(tenant_id=user_tenant(db, current_user), owner_user_id=owner, kind="image", stored_name=stored_name, original_name=(filename or "")[:255] or None,
                       content_type=info.mime, size=size, width=info.width, height=info.height, sha256=info.sha256, scan_status=info.scan_status)
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


# ---------------------------------------------------------------- reading and access

def get_asset(db: Session, asset_id: str, *, include_deleted: bool = False) -> MediaAsset:
    try:
        key = UUID(str(asset_id))
    except (ValueError, TypeError):
        raise _err(404, "Image not found")
    asset = db.get(MediaAsset, key)
    if asset is None or (asset.is_deleted and not include_deleted):
        raise _err(404, "Image not found")
    return asset


def lookup(db: Session, asset_ids) -> dict[str, MediaAsset]:
    keys = []
    for value in asset_ids:
        try:
            keys.append(UUID(str(value)))
        except (ValueError, TypeError):
            continue
    if not keys:
        return {}
    return {str(a.id): a for a in db.query(MediaAsset).filter(MediaAsset.id.in_(keys)).all()}


def file_path(asset: MediaAsset) -> Path:
    from app.services.training_upload_service import resolve_training_upload

    return resolve_training_upload(asset.stored_name)


def file_exists(asset: MediaAsset) -> bool:
    try:
        return file_path(asset).is_file()
    except HTTPException:
        return False


def is_staff_of(db: Session, current_user: dict, asset: MediaAsset, access_token: str | None = None) -> bool:
    role = current_user.get("role")
    if role == "super_admin":
        from app.services.super_admin_identity import profile_status_is_active

        return profile_status_is_active(current_user)
    if role not in ("admin", "provider"):
        return False
    tenant = user_tenant(db, current_user, access_token)
    return tenant is not None and asset.tenant_id is not None and tenant == asset.tenant_id


def _mentions(document: dict | None, asset_id: str) -> bool:
    return bool(document) and asset_id in json.dumps(document, default=str)


def _lesson_alive(db: Session, cache: dict, ebook: TrainingEbook) -> bool:
    """An eBook whose training or lesson was deleted no longer uses its pictures."""
    from app.models.training_model import Training

    if ebook.training_id not in cache:
        cache[ebook.training_id] = db.get(Training, ebook.training_id)
    training = cache[ebook.training_id]
    if training is None or training.is_deleted:
        return False
    return any(l.get("id") == ebook.lesson_id for sec in training.sections or [] for l in (sec.get("lessons") or sec.get("items") or []))


def references(db: Session, asset: MediaAsset) -> list[dict]:
    """The eBooks that still use this picture."""
    query = db.query(TrainingEbook)
    if asset.tenant_id is not None:
        query = query.filter(TrainingEbook.tenant_id == asset.tenant_id)
    out = []
    needle = str(asset.id)
    trainings: dict = {}
    for ebook in query.all():
        if not _lesson_alive(db, trainings, ebook):
            continue
        for where, document in (("draft", ebook.draft), ("published", ebook.published)):
            if _mentions(document, needle):
                out.append({"training_id": str(ebook.training_id), "section_id": ebook.section_id, "lesson_id": ebook.lesson_id, "in": where})
    return out


def learner_can_view(db: Session, current_user: dict, asset: MediaAsset) -> bool:
    """An enrolled learner of a training whose published eBook uses the picture."""
    from app.models.training_model import Training, TrainingEnrolment
    from app.services.training_service import ACTIVE_ENROLMENT_STATUSES

    email = current_user.get("email")
    if not email:
        return False
    needle = str(asset.id)
    for ebook in db.query(TrainingEbook).filter(TrainingEbook.published.isnot(None)).all():
        if asset.tenant_id is not None and ebook.tenant_id != asset.tenant_id:
            continue
        if not _mentions(ebook.published, needle):
            continue
        training = db.get(Training, ebook.training_id)
        if training is None or training.is_deleted or training.status in ("draft", "cancelled", "archived"):
            continue
        enrolled = db.query(TrainingEnrolment.id).filter(
            TrainingEnrolment.training_id == ebook.training_id, TrainingEnrolment.participant_email == email,
            TrainingEnrolment.status.in_(ACTIVE_ENROLMENT_STATUSES)).first()
        if enrolled:
            return True
    return False


def assert_can_view_file(db: Session, current_user: dict, asset: MediaAsset, access_token: str | None = None) -> None:
    if is_staff_of(db, current_user, asset, access_token) or learner_can_view(db, current_user, asset):
        return
    raise _err(404, "Image not found")           # not 403: whether someone else's picture exists is not for this person to learn


def delete_asset(db: Session, current_user: dict, asset: MediaAsset, access_token: str | None = None) -> None:
    if not is_staff_of(db, current_user, asset, access_token):
        raise _err(404, "Image not found")
    used = references(db, asset)
    if used:
        raise HTTPException(status_code=409, detail={
            "code": "ASSET_IN_USE", "message": "This image is still used by an eBook. Remove it from the pages first, then delete it", "used_by": used})
    try:
        file_path(asset).unlink(missing_ok=True)
    except HTTPException:
        pass
    asset.is_deleted, asset.deleted_at = True, datetime.utcnow()
    db.commit()


def asset_summary(asset: MediaAsset) -> dict:
    return {"asset_id": str(asset.id), "url": file_url(asset), "name": asset.original_name, "size": asset.size, "type": asset.content_type, "width": asset.width,
            "height": asset.height, "sha256": asset.sha256, "scan_status": asset.scan_status, "created_at": asset.created_at}
