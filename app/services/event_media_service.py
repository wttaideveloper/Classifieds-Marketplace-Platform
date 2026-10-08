"""Event media: validated uploads for the Event form's primary image, gallery, videos and documents.

Contract in one paragraph: the client uploads each file once (``POST /events/media``) and gets back an
asset with a hosted ``url``. The Event create/update JSON keeps carrying plain URL strings (documents:
objects) exactly as before — nothing about the stored shape changes — and the server verifies on save
that any hosted URL belongs to the caller's tenant and fits the field. Referencing an asset from an
Event is what "completes" the upload; files nobody references are deleted after a grace period.
See docs/event-media-upload-contract.md.
"""
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlsplit

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import Text, cast, or_
from sqlalchemy.orm import Session

from app.models.event_media_model import EventMedia
from app.utils.public_urls import _is_ip, public_media_base, to_https

MB = 1024 * 1024
FIELDS = ("primary_image", "gallery_images", "videos", "documents")

# mime type -> accepted filename extensions
MIME_EXTS: dict[str, tuple[str, ...]] = {
    "image/jpeg": ("jpg", "jpeg"),
    "image/png": ("png",),
    "image/webp": ("webp",),
    "image/gif": ("gif",),
    "video/mp4": ("mp4", "m4v"),
    "video/webm": ("webm",),
    "video/quicktime": ("mov",),
    "application/pdf": ("pdf",),
    "application/msword": ("doc",),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ("docx",),
    "application/vnd.ms-excel": ("xls",),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ("xlsx",),
    "application/vnd.ms-powerpoint": ("ppt",),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ("pptx",),
}
# what the first bytes of a real file of that mime type look like
MIME_SIGNATURE = {
    "image/jpeg": "jpeg", "image/png": "png", "image/webp": "webp", "image/gif": "gif",
    "video/mp4": "ftyp", "video/quicktime": "ftyp", "video/webm": "ebml", "application/pdf": "pdf",
    "application/msword": "ole", "application/vnd.ms-excel": "ole", "application/vnd.ms-powerpoint": "ole",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "zip",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "zip",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "zip",
}

_IMAGES = ("image/jpeg", "image/png", "image/webp")
POLICY: dict[str, dict] = {
    "primary_image": {"mimes": _IMAGES, "max_bytes": 5 * MB, "max_count": 1},
    "gallery_images": {"mimes": _IMAGES + ("image/gif",), "max_bytes": 5 * MB, "max_count": 10},
    "videos": {"mimes": ("video/mp4", "video/webm", "video/quicktime"), "max_bytes": 100 * MB, "max_count": 3},
    "documents": {
        "mimes": (
            "application/pdf", "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.ms-excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-powerpoint", "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
        "max_bytes": 10 * MB, "max_count": 10,
    },
}

ORPHAN_TTL = timedelta(hours=24)
_MAX_URL_LENGTH = 2048
_HOSTED_RE = re.compile(r"/api/v1/events/media/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.([A-Za-z0-9]{1,10})$")
_CHUNK = 1024 * 1024


# --- policy ---------------------------------------------------------------------------------

def policy_payload() -> dict:
    out = {}
    for field, rule in POLICY.items():
        exts = sorted({e for mime in rule["mimes"] for e in MIME_EXTS[mime]})
        out[field] = {
            "mime_types": list(rule["mimes"]), "extensions": exts,
            "max_size_bytes": rule["max_bytes"], "max_count": rule["max_count"],
        }
    return out


def _sniff(head: bytes) -> str | None:
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:5] == b"%PDF-":
        return "pdf"
    if head[4:8] == b"ftyp":
        return "ftyp"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "ebml"
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole"
    return None


# --- storage --------------------------------------------------------------------------------

def media_root() -> Path:
    from app.services.attachment_storage import ensure_upload_directory

    root = ensure_upload_directory() / "events"
    root.mkdir(parents=True, exist_ok=True)
    return root


def asset_path(asset: EventMedia) -> Path:
    return media_root() / f"{asset.id}.{asset.ext}"


ASSET_FILE_RE = re.compile(
    r"^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.([A-Za-z0-9]{1,10})$"
)


def diagnose_asset_file(db: Session, asset_file: str) -> dict:
    """Why `GET /events/media/<asset_file>` answers 404 "File not found", or that it should not.

    That one response covers four different problems; this tells them apart:
      invalid_name          the name is not <uuid>.<ext>
      no_database_record    there is no event_media row with that id (this database never saw the upload)
      extension_mismatch    the row exists but was stored with a different extension than the URL asks for
      file_missing_on_disk  the row exists and matches, but the file is not under UPLOAD_DIR/events
      ok                    row and file both exist (a 404 then is not from this lookup)
    Also reports where the file is expected, what is actually on disk for that id, and which Events use it."""
    out: dict = {"asset_file": asset_file, "verdict": None}
    root = media_root()
    out["storage_dir"] = str(root)
    match = ASSET_FILE_RE.match(asset_file or "")
    if not match:
        out["verdict"] = "invalid_name"
        return out
    asset_id, url_ext = uuid.UUID(match.group(1)), match.group(2).lower()
    out["asset_id"], out["url_extension"] = str(asset_id), url_ext
    out["files_on_disk_for_this_id"] = sorted(p.name for p in root.glob(f"{asset_id}.*"))

    asset = db.get(EventMedia, asset_id)
    if asset is not None:
        out["record"] = {
            "field": asset.field, "extension": asset.ext, "mime_type": asset.mime_type, "size": asset.size,
            "tenant_id": str(asset.tenant_id), "uploaded_by": asset.uploaded_by,
            "created_at": asset.created_at.isoformat() if asset.created_at else None,
            "attached_at": asset.attached_at.isoformat() if asset.attached_at else None,
            "released_at": asset.released_at.isoformat() if asset.released_at else None,
        }
        out["expected_path"] = str(asset_path(asset))

    from app.models.event_model import Event

    out["used_by_events"] = [
        {"id": str(e.id), "title": e.title, "status": e.status, "deleted": bool(e.is_deleted)}
        for e in db.query(Event).filter(_reference_filter(asset_id)).limit(10).all()
    ]

    if asset is None:
        out["verdict"] = "no_database_record"
    elif asset.ext != url_ext:
        out["verdict"] = "extension_mismatch"
    elif not asset_path(asset).is_file():
        out["verdict"] = "file_missing_on_disk"
    else:
        out["verdict"] = "ok"
        on_disk = asset_path(asset).stat().st_size
        out["size_on_disk"] = on_disk
        if on_disk != asset.size:
            out["note"] = f"file size on disk ({on_disk}) differs from the recorded size ({asset.size})"
    return out


def scan_event_media(db: Session) -> dict:
    """Compare every event_media row with the files under UPLOAD_DIR/events."""
    root = media_root()
    on_disk = {p.name for p in root.iterdir() if p.is_file()}
    expected: set[str] = set()
    missing_attached, missing_pending = [], []
    for asset in db.query(EventMedia).all():
        name = f"{asset.id}.{asset.ext}"
        expected.add(name)
        if name not in on_disk:
            (missing_attached if asset.attached_at else missing_pending).append(name)
    return {
        "storage_dir": str(root),
        "records": len(expected),
        "files_on_disk": len(on_disk),
        "records_with_missing_file_used_by_an_event": sorted(missing_attached),
        "records_with_missing_file_never_attached": sorted(missing_pending),
        "files_on_disk_without_a_record": sorted(on_disk - expected),
    }


def asset_url(asset: EventMedia) -> str:
    return to_https(f"{public_media_base()}/api/v1/events/media/{asset.id}.{asset.ext}")


def asset_dict(asset: EventMedia) -> dict:
    return {
        "id": str(asset.id), "field": asset.field, "url": asset_url(asset), "name": asset.original_name,
        "size": asset.size, "type": asset.mime_type, "attached": asset.attached_at is not None,
        "expires_at": None if asset.attached_at else (asset.created_at + ORPHAN_TTL).isoformat(),
    }


def store_upload(
    db: Session, *, file: UploadFile, field: str, tenant_id: uuid.UUID, enterprise_id: uuid.UUID | None,
    uploaded_by: str | None, client_ref: str | None,
) -> tuple[EventMedia, bool]:
    """Validate and save one file. Returns (asset, created); created is False when ``client_ref``
    matched an earlier upload by the same user (a retry), which returns that asset untouched."""
    if field not in POLICY:
        raise HTTPException(422, f"field must be one of {list(FIELDS)}")
    rule = POLICY[field]

    if client_ref:
        existing = db.query(EventMedia).filter(
            EventMedia.uploaded_by == uploaded_by, EventMedia.client_ref == client_ref,
            EventMedia.tenant_id == tenant_id, EventMedia.field == field,
        ).first()
        if existing is not None and asset_path(existing).is_file():
            return existing, False

    mime = (file.content_type or "").split(";")[0].strip().lower()
    if mime not in rule["mimes"]:
        raise HTTPException(415, f"{field} accepts {sorted(rule['mimes'])}; got '{mime or 'unknown'}'")
    original = Path(file.filename or "upload").name[:255] or "upload"
    ext = Path(original).suffix.lstrip(".").lower()
    if ext not in MIME_EXTS[mime]:
        raise HTTPException(415, f"File extension '.{ext}' does not match {mime} (expected {list(MIME_EXTS[mime])})")

    asset_id = uuid.uuid4()
    target = media_root() / f"{asset_id}.{ext}"
    size, head = 0, b""
    try:
        with open(target, "wb") as out:
            while True:
                chunk = file.file.read(_CHUNK)
                if not chunk:
                    break
                if size == 0:
                    head = chunk[:16]
                size += len(chunk)
                if size > rule["max_bytes"]:
                    raise HTTPException(413, f"{field} files may be at most {rule['max_bytes'] // MB} MB")
                out.write(chunk)
        if size == 0:
            raise HTTPException(400, "Uploaded file is empty")
        if _sniff(head) != MIME_SIGNATURE[mime]:
            raise HTTPException(415, f"File content is not a valid {mime}")
    except HTTPException:
        target.unlink(missing_ok=True)
        raise
    except OSError as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(500, "Failed to store file") from exc

    asset = EventMedia(
        id=asset_id, tenant_id=tenant_id, enterprise_id=enterprise_id, uploaded_by=uploaded_by, field=field,
        original_name=original, ext=ext, mime_type=mime, size=size, client_ref=client_ref or None,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    try:  # best-effort housekeeping; never fails an upload
        purge_unreferenced_event_media(db)
    except Exception:
        db.rollback()
    return asset, True


# --- references -----------------------------------------------------------------------------

def parse_hosted_url(url: str) -> tuple[uuid.UUID, str] | None:
    match = _HOSTED_RE.search(urlsplit(url).path)
    if not match:
        return None
    return uuid.UUID(match.group(1)), match.group(2).lower()


def _items(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _urls_of(value) -> list[str]:
    out = []
    for item in _items(value):
        url = item.get("url") if isinstance(item, dict) else item
        if isinstance(url, str):
            out.append(url)
    return out


def referenced_asset_ids(event) -> set[uuid.UUID]:
    ids = set()
    for field in FIELDS:
        for url in _urls_of(getattr(event, field, None)):
            parsed = parse_hosted_url(url)
            if parsed:
                ids.add(parsed[0])
    return ids


def _reference_filter(asset_id: uuid.UUID, fields: tuple[str, ...] = FIELDS):
    from app.models.event_model import Event

    needle = str(asset_id)
    clauses = []
    for field in fields:
        column = getattr(Event, field)
        clauses.append(column.contains(needle, autoescape=True) if field == "primary_image"
                       else cast(column, Text).contains(needle, autoescape=True))
    return or_(*clauses)


def is_referenced(db: Session, asset_id: uuid.UUID) -> bool:
    from app.models.event_model import Event

    return db.query(Event.id).filter(_reference_filter(asset_id)).first() is not None


def is_published_cover(db: Session, asset_id: uuid.UUID) -> bool:
    """Used as primary image or in the gallery of a published, non-deleted Event."""
    from app.models.event_model import Event

    return db.query(Event.id).filter(
        Event.status == "published", Event.is_deleted.is_(False),
        _reference_filter(asset_id, ("primary_image", "gallery_images")),
    ).first() is not None


def is_published_reference(db: Session, asset_id: uuid.UUID) -> bool:
    from app.models.event_model import Event

    return db.query(Event.id).filter(
        Event.status == "published", Event.is_deleted.is_(False), _reference_filter(asset_id),
    ).first() is not None


# --- validating what an Event create/update carries -----------------------------------------

def _clean_url(db: Session, raw, field: str, tenant_id, problems: list, assets: dict) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        problems.append(f"{field}: each entry needs a url")
        return None
    url = raw.strip()
    if len(url) > _MAX_URL_LENGTH:
        problems.append(f"{field}: url is longer than {_MAX_URL_LENGTH} characters")
        return None
    hosted = parse_hosted_url(url)
    if hosted:
        asset = db.get(EventMedia, hosted[0])
        # same answer for "missing" and "someone else's" so asset ids cannot be probed across tenants
        if asset is None or asset.tenant_id != tenant_id or asset.ext != hosted[1]:
            problems.append(f"{field}: unknown or unauthorized uploaded file {hosted[0]}")
            return None
        if asset.mime_type not in POLICY[field]["mimes"]:
            problems.append(f"{field}: {asset.original_name} is {asset.mime_type}, which this field does not accept")
            return None
        assets[asset.id] = asset
        return asset_url(asset)
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        problems.append(f"{field}: only an uploaded file or an https:// URL is accepted")
        return None
    host = parts.hostname or ""
    if any(ch.isspace() for ch in url):
        problems.append(f"{field}: a URL must not contain spaces")
    elif parts.username or parts.password:
        problems.append(f"{field}: a URL must not contain a username or password")
    elif not host or "." not in host or host == "localhost" or _is_ip(host):
        problems.append(f"{field}: a URL must use a public hostname (not an IP address or localhost)")
    else:
        return to_https(url)
    return None


def normalize_event_media(db: Session, tenant_id, values: dict) -> tuple[dict, set[uuid.UUID]]:
    """Validate the media fields present in ``values`` for an Event owned by ``tenant_id``.

    Returns the values to store (hosted URLs re-issued in canonical https form; hosted documents
    expanded to {id,url,name,size,type} from our own record, never from client-supplied metadata)
    and the ids of the hosted assets they use. Raises 422 listing every problem found.
    """
    problems: list[str] = []
    assets: dict[uuid.UUID, EventMedia] = {}
    out: dict = {}

    for field in FIELDS:
        if field not in values:
            continue
        raw = values[field]
        if field == "primary_image":
            if raw in (None, ""):
                out[field] = None
            else:
                out[field] = _clean_url(db, raw.get("url") if isinstance(raw, dict) else raw, field, tenant_id, problems, assets)
            continue

        if raw is None:
            out[field] = []
            continue
        if not isinstance(raw, list):
            problems.append(f"{field}: must be a list")
            continue
        seen, items = set(), []
        for entry in raw:
            url = entry.get("url") if isinstance(entry, dict) else entry
            clean = _clean_url(db, url, field, tenant_id, problems, assets)
            if clean is None or clean in seen:
                continue
            seen.add(clean)
            items.append((clean, entry))
        if len(items) > POLICY[field]["max_count"]:
            problems.append(f"{field}: at most {POLICY[field]['max_count']} allowed, got {len(items)}")
        out[field] = [_store_shape(field, clean, entry, assets) for clean, entry in items]

    if problems:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"message": "Invalid event media", "errors": problems})
    return out, set(assets)


def _store_shape(field: str, url: str, entry, assets: dict):
    hosted = parse_hosted_url(url)
    if field != "documents":
        return url
    if hosted:
        asset = assets[hosted[0]]
        return {"id": str(asset.id), "url": url, "name": asset.original_name, "size": asset.size, "type": asset.mime_type}
    # A pasted link. Always stored as an object with at least {url, name} so the UI renders documents
    # uniformly; any name the client gave wins, otherwise it is taken from the end of the URL.
    kept = {"url": url}
    if isinstance(entry, dict):
        for key in ("name", "size", "type"):
            if entry.get(key) is not None:
                kept[key] = str(entry[key])[:255] if key != "size" else entry[key]
    if not kept.get("name"):
        kept["name"] = _name_from_url(url)
    return kept


def _name_from_url(url: str) -> str:
    parts = urlsplit(url)
    last = unquote(parts.path.rsplit("/", 1)[-1]).strip()
    return (last or parts.hostname or "document")[:255]


def sync_event_media(db: Session, event, before_ids: set[uuid.UUID]) -> None:
    """After the Event is saved: mark newly referenced assets attached, and start the grace clock on
    assets this save stopped referencing."""
    now = datetime.utcnow()
    after_ids = referenced_asset_ids(event)
    if after_ids:
        for asset in db.query(EventMedia).filter(EventMedia.id.in_(after_ids)).all():
            asset.attached_at = asset.attached_at or now
            asset.released_at = None
    released = before_ids - after_ids
    if released:
        for asset in db.query(EventMedia).filter(EventMedia.id.in_(released)).all():
            asset.released_at = now
    if after_ids or released:
        db.commit()


def purge_unreferenced_event_media(db: Session, now: datetime | None = None, limit: int = 50) -> int:
    """Delete files nothing references any more: uploads never used by a saved Event, and files an
    update removed — each after ORPHAN_TTL, and only if no Event (e.g. a duplicate) still uses them."""
    now = now or datetime.utcnow()
    cutoff = now - ORPHAN_TTL
    candidates = db.query(EventMedia).filter(
        or_(
            (EventMedia.attached_at.is_(None)) & (EventMedia.created_at < cutoff),
            EventMedia.released_at < cutoff,
        )
    ).order_by(EventMedia.created_at).limit(limit).all()
    removed = 0
    for asset in candidates:
        if is_referenced(db, asset.id):
            continue
        asset_path(asset).unlink(missing_ok=True)
        db.delete(asset)
        removed += 1
    if removed:
        db.commit()
    return removed
