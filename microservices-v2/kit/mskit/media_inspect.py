"""Pure image inspection (copied from app/services/media_service.py: FORMATS, ImageInfo, inspect_image).
Used by every service that accepts images; no database, no storage."""
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID
from fastapi import HTTPException
from mskit.settings import settings

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


