"""Inline (base64 data URI) images on Products and Services become hosted files with https URLs.

Products keep their images in one text column (`product_images`, sent as `image_urls` or `product_images`) and Services one banner (`banner_image`). Some clients
saved the pictures themselves as `data:image/...;base64,...` there: megabytes per row, sent in every list response, and impossible to split on commas because a
data URI contains one.

* Write time: `externalize_payload` (called by the product / service repositories) stores every data URI as a file with the same checks as
  `POST /products/upload` (JPEG, PNG, WebP or GIF; at most PRODUCT_MAX_IMAGE_MB; the bytes must really be that type) and keeps its URL instead.
  The column then holds URLs only, comma separated, which is the format existing clients already read.
* Read time: `url_list` gives the images as a clean list of http(s) URLs for the `images` response field. A data URI never appears in it.
* Rows saved before this: scripts/externalize_inline_images.py converts them (dry run by default).
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import uuid

from fastapi import HTTPException

from app.core.config import settings

MB = 1024 * 1024
DATA_URI = re.compile(r"data:(?P<mime>[\w.+-]+/[\w.+-]+)?(?P<params>(?:;[\w.+-]+=[\w.+-]+)*);base64,(?P<data>[A-Za-z0-9+/=\s]+)", re.IGNORECASE)
HTTP_URL = re.compile(r"https?://[^\s,\"'\]]+", re.IGNORECASE)
RELATIVE_OWN = re.compile(r"/api/v1/[^\s,\"'\]]+")
IMAGE_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}

PRODUCT_IMAGE_KEYS = ("product_images",)
SERVICE_IMAGE_KEYS = ("banner_image",)


INLINE_MARK = re.compile(r"data:[^,\s]*;base64,", re.IGNORECASE)


def has_inline(text) -> bool:
    return isinstance(text, str) and INLINE_MARK.search(text) is not None


def entries(text) -> list[str]:
    """Every image of a stored value, in order: data URIs, absolute URLs and our own relative /api/v1/... paths. Accepts a JSON list, a comma list or one value."""
    if text is None:
        return []
    if isinstance(text, (list, tuple)):
        return [e for item in text for e in entries(item)]
    value = str(text).strip()
    if not value:
        return []
    if value.startswith("["):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [e for item in parsed for e in entries(item)]
        except ValueError:
            pass
    found: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []
    for match in DATA_URI.finditer(value):
        found.append((match.start(), re.sub(r"\s+", "", match.group(0))))
        taken.append(match.span())

    def free(span):
        return all(span[1] <= a or span[0] >= b for a, b in taken)

    for pattern in (HTTP_URL, RELATIVE_OWN):
        for match in pattern.finditer(value):
            if free(match.span()):
                found.append((match.start(), match.group(0)))
                taken.append(match.span())
    return [item for _pos, item in sorted(found)]


def url_list(text) -> list[str]:
    """Only the hosted images, for clients: absolute http(s) URLs (our own relative paths are made absolute when a public base is configured)."""
    from app.utils.public_urls import public_media_base, to_https

    base = public_media_base()
    out = []
    for item in entries(text):
        if item.lower().startswith("data:"):
            continue
        if item.startswith("/") and base:
            item = f"{base}{item}"             # without a configured public base our own path stays relative rather than being dropped
        out.append(to_https(item))
    return out


def store_data_uri(uri: str) -> str:
    """Decode, check and store one data URI; its public URL."""
    from app.services.event_media_service import MIME_SIGNATURE, _sniff
    from app.services.product_media_service import media_root, public_url

    match = DATA_URI.fullmatch(re.sub(r"\s+", "", uri))
    mime = ((match.group("mime") if match else "") or "").lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    if mime not in IMAGE_EXT:
        raise HTTPException(415, f"Unsupported inline image type '{mime or 'unknown'}'. Use JPEG, PNG, WebP or GIF, or upload with POST /api/v1/products/upload")
    try:
        data = base64.b64decode(match.group("data"), validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "An inline image is not valid base64")
    if not data:
        raise HTTPException(422, "An inline image is empty")
    if len(data) > settings.PRODUCT_MAX_IMAGE_MB * MB:
        raise HTTPException(413, f"Photos may be at most {settings.PRODUCT_MAX_IMAGE_MB} MB")
    if _sniff(data[:16]) != MIME_SIGNATURE[mime]:
        raise HTTPException(415, f"An inline image's contents are not a real {mime}")
    stored = f"{uuid.uuid4().hex}.{IMAGE_EXT[mime]}"
    (media_root() / stored).write_bytes(data)
    return public_url(stored)


def externalize(text) -> str | None:
    """The value with every data URI replaced by the URL of a stored file, as a comma list. Values without a data URI are returned unchanged."""
    if not has_inline(text):
        return text
    if INLINE_MARK.search(DATA_URI.sub("", text)):            # a "data:...;base64," whose data is not base64 at all
        raise HTTPException(422, "An inline image is not valid base64")
    return ", ".join(store_data_uri(item) if item.lower().startswith("data:") else item for item in entries(text)) or None


def externalize_payload(payload: dict, keys) -> dict:
    for key in keys:
        if key in payload and has_inline(payload[key]):
            payload[key] = externalize(payload[key])
    return payload
