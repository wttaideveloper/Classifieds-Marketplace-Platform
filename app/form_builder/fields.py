"""The common field model for the Event, Training and Blog form builders.

Fourteen explicit field types (never folded into text / textarea / select):

    short_text  rich_text  long_text  dropdown  radio  checkbox  number  date  time  reference_url  additional_images  supporting_document  video  tags

`tags` (a short list of free-text labels) is the one type beyond the thirteen the product asked for: Blog posts already have tags.

A field, as stored and as the Super Admin's builder sees it:

    id, key, label, type, renderer, placeholder, help_text, required, enabled, order, validation, options, visibility, system

`id` is assigned once and never changes; `key` is the stable name values are stored under and cannot be changed after creation; `renderer` tells
a client which control to draw (it is fixed by `type`); `visibility` is `published` (shown in the public and mobile APIs) or
`enterprise_admin_only` (never leaves the Enterprise Admin's own screens); `system` marks a protected field the module owns.

`normalize_field` checks what a Super Admin sent for a field. `validate_value` checks a value somebody typed into a form against the field.
Both append `{field, code, message}` to an `errors` list instead of raising, so every problem is reported at once.
"""
import re
import uuid
from datetime import date, time
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from app.form_builder.urls import UnsafeUrl, inspect_url

KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE = re.compile(r"^\+?[0-9][0-9 ()\-]{5,18}$")
TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?$")
MAX_OPTIONS = 50
VISIBILITIES = ("published", "enterprise_admin_only")

IMAGE_TYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif"}
DOCUMENT_TYPES = {"pdf": "application/pdf", "doc": "application/msword", "xls": "application/vnd.ms-excel", "ppt": "application/vnd.ms-powerpoint",
                  "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                  "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                  "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation", "txt": "text/plain", "csv": "text/csv"}
VIDEO_TYPES = {"mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime"}

# type -> (renderer, takes options, which allowed file types, the largest upload the platform allows in MB)
TYPES = {
    "short_text": {"renderer": "text_input", "options": False},
    "rich_text": {"renderer": "rich_text_editor", "options": False},
    "long_text": {"renderer": "textarea", "options": False},
    "dropdown": {"renderer": "select", "options": True},
    "radio": {"renderer": "radio_group", "options": True},
    "checkbox": {"renderer": "checkbox_group", "options": True},
    "number": {"renderer": "number_input", "options": False},
    "date": {"renderer": "date_picker", "options": False},
    "time": {"renderer": "time_picker", "options": False},
    "reference_url": {"renderer": "url_input", "options": False},
    "additional_images": {"renderer": "image_uploader", "options": False, "files": IMAGE_TYPES, "max_mb": 20},
    "supporting_document": {"renderer": "document_uploader", "options": False, "files": DOCUMENT_TYPES, "max_mb": 50},
    "video": {"renderer": "video_input", "options": False, "files": VIDEO_TYPES, "max_mb": 500},
    "tags": {"renderer": "tag_input", "options": False},
    # Module types: the Event and Training forms have them (their dates are one value with a time; their yes/no switches). Blog does not offer them.
    "datetime": {"renderer": "datetime_picker", "options": False, "module": True},
    "toggle": {"renderer": "switch", "options": False, "module": True},
}
# Names older code or clients used. Accepted on input and turned into the explicit type, never returned.
ALIASES = {"text": "short_text", "textarea": "long_text", "select": "dropdown", "multiselect": "checkbox", "url": "reference_url", "richtext": "rich_text",
           "images": "additional_images", "document": "supporting_document", "documents": "supporting_document"}
MEDIA = ("additional_images", "supporting_document", "video")
ALLOWED_PROVIDERS = ("youtube", "vimeo", "external")

# rule -> (kind, low, high). kinds: int, number, bool, date, time, regex, enum(low = the allowed values), files(low = the types the field may allow)
RULES = {
    "short_text": {"min_length": ("int", 0, 500), "max_length": ("int", 1, 500), "pattern": ("regex", 200, None), "format": ("enum", ("email", "phone"), None)},
    "long_text": {"min_length": ("int", 0, 10000), "max_length": ("int", 1, 10000)},
    "rich_text": {"min_length": ("int", 0, 100000), "max_length": ("int", 1, 100000)},
    "dropdown": {}, "radio": {},
    "checkbox": {"min_items": ("int", 0, MAX_OPTIONS), "max_items": ("int", 1, MAX_OPTIONS)},
    "number": {"min": ("number", None, None), "max": ("number", None, None), "decimals": ("int", 0, 6)},
    "date": {"min": ("date", None, None), "max": ("date", None, None), "not_in_past": ("bool", None, None), "not_in_future": ("bool", None, None)},
    "time": {"min": ("time", None, None), "max": ("time", None, None)},
    "reference_url": {"allowed_providers": ("providers", None, None), "require_https": ("bool", None, None), "max_items": ("int", 1, 20)},
    "tags": {"max_items": ("int", 1, 20), "max_length": ("int", 1, 50)},
    "datetime": {"min": ("datetime", None, None), "max": ("datetime", None, None), "not_in_past": ("bool", None, None), "not_in_future": ("bool", None, None)},
    "toggle": {},
    "additional_images": {"min_items": ("int", 0, 50), "max_items": ("int", 1, 50), "max_file_size_mb": ("number", 0.1, 20), "allowed_types": ("files", None, None)},
    "supporting_document": {"min_items": ("int", 0, 50), "max_items": ("int", 1, 50), "max_file_size_mb": ("number", 0.1, 50), "allowed_types": ("files", None, None)},
    "video": {"min_items": ("int", 0, 10), "max_items": ("int", 1, 10), "max_file_size_mb": ("number", 0.1, 500), "allowed_types": ("files", None, None),
              "allowed_providers": ("providers", None, None), "allow_upload": ("bool", None, None)},
}
DEFAULT_MAX_ITEMS = {"additional_images": 10, "supporting_document": 10, "video": 3}


def err(field: str, code: str, message: str) -> dict:
    return {"field": field, "code": code, "message": message}


def clean_text(value, limit: int) -> str | None:
    text = " ".join(str(value).split()) if value is not None else ""
    return text[:limit] or None


def is_empty(value) -> bool:
    return value is None or value == "" or value == [] or value == {}


def renderer_of(field_type: str) -> str:
    return TYPES[field_type]["renderer"]


def canonical_type(raw) -> str | None:
    value = str(raw or "").strip().lower()
    value = ALIASES.get(value, value)
    return value if value in TYPES else None


def canonical_visibility(raw, *, legacy_enterprise_only: bool = False) -> str | None:
    value = str(raw or "published").strip().lower()
    value = {"public": "published", "internal": "enterprise_admin_only", "enterprise_only": "enterprise_admin_only"}.get(value, value)
    if legacy_enterprise_only:
        value = "enterprise_admin_only"
    return value if value in VISIBILITIES else None


# ---------------------------------------------------------------- the builder side: checking a field definition

def _parse_date(value):
    return date.fromisoformat(str(value))


def _parse_datetime(value):
    from datetime import datetime as _dt

    text = str(value).strip().replace("Z", "+00:00")
    moment = _dt.fromisoformat(text)
    return moment.replace(tzinfo=None) if moment.tzinfo is None else moment.astimezone(__import__("datetime").timezone.utc).replace(tzinfo=None)


def _parse_time(value):
    match = TIME_PATTERN.match(str(value))
    if not match:
        raise ValueError
    return time(int(match.group(1)), int(match.group(2)))


def check_rules(field_type: str, raw, path: str, errors: list) -> dict:
    spec, out = RULES[field_type], {}
    for name, value in (raw or {}).items():
        if name not in spec:
            errors.append(err(f"{path}.validation.{name}", "UNKNOWN_RULE", f"'{name}' is not a rule of a {field_type} field"))
            continue
        kind, low, high = spec[name]
        where = f"{path}.validation.{name}"
        if value is None:
            continue
        if kind == "int":
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                errors.append(err(where, "INVALID_RULE", f"{name} must be a whole number from {low} to {high}"))
                continue
        elif kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or (low is not None and not low <= value <= high):
                errors.append(err(where, "INVALID_RULE", f"{name} must be a number" + (f" from {low} to {high}" if low is not None else "")))
                continue
        elif kind == "bool":
            if not isinstance(value, bool):
                errors.append(err(where, "INVALID_RULE", f"{name} must be true or false"))
                continue
        elif kind == "date":
            try:
                value = _parse_date(value).isoformat()
            except ValueError:
                errors.append(err(where, "INVALID_RULE", f"{name} must be a date like 2026-01-31"))
                continue
        elif kind == "time":
            try:
                value = _parse_time(value).strftime("%H:%M")
            except ValueError:
                errors.append(err(where, "INVALID_RULE", f"{name} must be a time like 09:30"))
                continue
        elif kind == "datetime":
            try:
                value = _parse_datetime(value).isoformat()
            except ValueError:
                errors.append(err(where, "INVALID_RULE", f"{name} must be a date and time like 2026-01-31T09:30"))
                continue
        elif kind == "regex":
            try:
                if len(str(value)) > low:
                    raise re.error("long")
                re.compile(str(value))
                value = str(value)
            except re.error:
                errors.append(err(where, "INVALID_RULE", f"{name} must be a valid regular expression of at most {low} characters"))
                continue
        elif kind == "enum":
            if value not in low:
                errors.append(err(where, "INVALID_RULE", f"{name} must be one of: {', '.join(low)}"))
                continue
        elif kind == "providers":
            if not isinstance(value, list) or not value or any(v not in ALLOWED_PROVIDERS for v in value):
                errors.append(err(where, "INVALID_RULE", f"{name} must be a list of: {', '.join(ALLOWED_PROVIDERS)}"))
                continue
            value = list(dict.fromkeys(value))
        elif kind == "files":
            allowed = TYPES[field_type]["files"]
            if not isinstance(value, list) or not value or any(str(v).lower() not in allowed for v in value):
                errors.append(err(where, "INVALID_RULE", f"{name} must be a list of: {', '.join(sorted(allowed))}"))
                continue
            value = [str(v).lower() for v in dict.fromkeys(value)]
        out[name] = value
    for low_name, high_name in (("min_length", "max_length"), ("min_items", "max_items"), ("min", "max")):
        if low_name in out and high_name in out and out[low_name] > out[high_name]:
            errors.append(err(f"{path}.validation.{low_name}", "INVALID_RULE", f"{low_name} cannot be more than {high_name}"))
    if out.get("not_in_past") and out.get("not_in_future"):
        errors.append(err(f"{path}.validation.not_in_past", "INVALID_RULE", "A date cannot be required to be neither in the past nor in the future"))
    return out


def check_options(raw, path: str, errors: list, *, minimum: int = 1) -> list[dict]:
    out, values, labels = [], set(), set()
    if not isinstance(raw, list) or len(raw) < minimum:
        errors.append(err(f"{path}.options", "REQUIRED", f"Add at least {minimum} option" + ("s" if minimum > 1 else "")))
        return out
    if len(raw) > MAX_OPTIONS:
        errors.append(err(f"{path}.options", "TOO_MANY", f"At most {MAX_OPTIONS} options"))
    for i, item in enumerate(raw[:MAX_OPTIONS]):
        value = clean_text(item.get("value") if isinstance(item, dict) else item, 100)
        label = clean_text(item.get("label") if isinstance(item, dict) else item, 120) or value
        if not value:
            errors.append(err(f"{path}.options[{i}]", "REQUIRED", "An option needs a value"))
        elif value.lower() in values:
            errors.append(err(f"{path}.options[{i}]", "DUPLICATE_OPTION", f"The option '{value}' is listed twice"))
        elif label.lower() in labels:
            errors.append(err(f"{path}.options[{i}]", "DUPLICATE_OPTION", f"Two options are labelled '{label}'"))
        else:
            values.add(value.lower())
            labels.add(label.lower())
            out.append({"value": value, "label": label})
    return out


def normalize_field(raw, *, path: str, errors: list, existing: dict | None = None, system: bool = False) -> dict | None:
    """The cleaned field, or None after adding to `errors`. `existing` is the stored field being edited: its id, key and `system` flag are kept."""
    if not isinstance(raw, dict):
        errors.append(err(path, "INVALID", "A field must be an object"))
        return None
    key = str(raw.get("key") or (existing or {}).get("key") or "").strip()
    if not KEY_PATTERN.match(key):
        errors.append(err(f"{path}.key", "INVALID_KEY", "A key starts with a letter and has 2 to 40 lowercase letters, digits or underscores"))
        return None
    if existing and existing.get("key") != key:
        errors.append(err(f"{path}.key", "KEY_IMMUTABLE", "A field's key cannot be changed. Add a new field instead"))
        return None
    field_type = canonical_type(raw.get("type") or (existing or {}).get("type"))
    if field_type is None:
        errors.append(err(f"{path}.type", "INVALID_TYPE", f"type must be one of: {', '.join(TYPES)}"))
        return None
    if existing and existing.get("type") != field_type:
        errors.append(err(f"{path}.type", "TYPE_IMMUTABLE", "A field's type cannot be changed once it exists, because values were saved with it. Add a new field instead"))
        return None
    label = clean_text(raw.get("label"), 120)
    if not label:
        errors.append(err(f"{path}.label", "REQUIRED", "A field needs a label"))
        return None
    visibility = canonical_visibility(raw.get("visibility"), legacy_enterprise_only=bool(raw.get("enterprise_only")))
    if visibility is None:
        errors.append(err(f"{path}.visibility", "INVALID", f"visibility must be one of: {', '.join(VISIBILITIES)}"))
        visibility = "published"
    enabled = raw.get("enabled", raw.get("visible", True))
    field_id = str((existing or {}).get("id") or "")
    if not field_id:
        try:
            field_id = str(uuid.UUID(str(raw.get("id")))) if raw.get("id") else str(uuid.uuid4())
        except ValueError:
            field_id = str(uuid.uuid4())
    options, source = [], (existing or {}).get("options_source") or (raw.get("options_source") if system else None)
    if TYPES[field_type]["options"] and source:
        options = []                                    # the choices come from elsewhere (for example the blog categories), not from the form
    elif TYPES[field_type]["options"]:
        options = check_options(raw.get("options"), path, errors, minimum=2 if field_type == "radio" else 1)
    elif raw.get("options"):
        errors.append(err(f"{path}.options", "OPTIONS_NOT_ALLOWED", f"A {field_type} field has no options"))
    return {
        "id": field_id, "key": key, "label": label, "type": field_type, "renderer": renderer_of(field_type),
        "placeholder": clean_text(raw.get("placeholder"), 120), "help_text": clean_text(raw.get("help_text"), 300),
        "required": bool(raw.get("required", False)) and bool(enabled), "enabled": bool(enabled), "order": int(raw.get("order") or (existing or {}).get("order") or 0),
        "validation": check_rules(field_type, raw.get("validation"), path, errors), "options": options, "visibility": visibility,
        "system": bool((existing or {}).get("system", system)),
        **({"options_source": source} if source else {}),
    }


# ---------------------------------------------------------------- the writer side: checking a value against a field

def _length_checks(field: dict, text: str, path: str, errors: list, *, default_max: int) -> None:
    rules, label, size = field.get("validation") or {}, field["label"], len(text)
    if size > rules.get("max_length", default_max):
        errors.append(err(path, "TOO_LONG", f"{label} can have at most {rules.get('max_length', default_max)} characters"))
    elif size < rules.get("min_length", 0):
        errors.append(err(path, "TOO_SHORT", f"{label} needs at least {rules['min_length']} characters"))


def _plain(html: str) -> str:
    return re.sub(r"<[^>]*>", "", html or "").strip()


def _ext(item: dict) -> str:
    name = str(item.get("name") or "") or urlsplit(str(item.get("url") or "")).path
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _media_item(field: dict, raw, path: str, errors: list) -> dict | None:
    rules, label, field_type = field.get("validation") or {}, field["label"], field["type"]
    item = {"url": raw} if isinstance(raw, str) else raw
    if not isinstance(item, dict) or not item.get("url"):
        errors.append(err(path, "INVALID_FORMAT", f"{label}: each item needs a url"))
        return None
    try:
        info = inspect_url(item["url"], allow_relative=True)
    except UnsafeUrl as exc:
        errors.append(err(path, exc.code, f"{label}: {exc.message}"))
        return None
    out: dict = {"url": info["url"]}
    if field_type == "video":
        providers = rules.get("allowed_providers", list(ALLOWED_PROVIDERS))
        if info["provider"] == "internal":
            if rules.get("allow_upload", True) is False:
                errors.append(err(path, "UPLOAD_NOT_ALLOWED", f"{label}: video uploads are not allowed, use a link"))
                return None
        elif info["provider"] not in providers:
            errors.append(err(path, "PROVIDER_NOT_ALLOWED", f"{label}: links from {', '.join(providers)} only"))
            return None
        out.update({"provider": "upload" if info["provider"] == "internal" else info["provider"], "video_id": info["video_id"], "thumbnail_url": info["thumbnail_url"]})
    for key in ("name", "alt", "caption", "file_id"):
        if item.get(key):
            out[key] = clean_text(item[key], 255 if key == "name" else 300)
    size = item.get("size")
    if size is not None:
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            errors.append(err(path, "INVALID_FORMAT", f"{label}: size must be a number of bytes"))
            return None
        limit_mb = rules.get("max_file_size_mb", TYPES[field_type]["max_mb"])
        if size > limit_mb * 1024 * 1024:
            errors.append(err(path, "FILE_TOO_LARGE", f"{label}: a file can be at most {limit_mb} MB"))
            return None
        out["size"] = size
    allowed = rules.get("allowed_types") or list(TYPES[field_type]["files"])
    kind = (item.get("content_type") or "").lower()
    ext = _ext(item)
    external_video = field_type == "video" and out.get("provider") in ("youtube", "vimeo", "external")
    if not external_video and field_type != "reference_url":
        known = TYPES[field_type]["files"]
        mime_ok = not kind or kind in {known[a] for a in allowed if a in known}
        ext_ok = not ext or ext in allowed
        if not mime_ok or not ext_ok:
            errors.append(err(path, "FILE_TYPE_NOT_ALLOWED", f"{label}: allowed file types are {', '.join(allowed)}"))
            return None
    if kind:
        out["content_type"] = kind
    return out


def validate_value(field: dict, value, path: str, errors: list, *, sanitize_html=None):
    """The stored form of one value, or None after adding to `errors`. Empty values are for the caller to drop (or to refuse when required)."""
    field_type, rules, label = field["type"], field.get("validation") or {}, field["label"]
    if field_type in ("short_text", "long_text", "rich_text"):
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            errors.append(err(path, "INVALID_TYPE", f"{label} must be text"))
            return None
        text = str(value).strip()
        if field_type == "rich_text":
            if sanitize_html:
                text = sanitize_html(text)
            _length_checks(field, _plain(text), path, errors, default_max=100000)
            return text
        _length_checks(field, text, path, errors, default_max=255 if field_type == "short_text" else 5000)
        if field_type == "short_text":
            if rules.get("pattern") and not re.search(rules["pattern"], text):
                errors.append(err(path, "INVALID_FORMAT", f"{label} is not in the expected format"))
            if rules.get("format") == "email" and not EMAIL.match(text):
                errors.append(err(path, "INVALID_FORMAT", f"{label} must be an email address"))
            if rules.get("format") == "phone" and not PHONE.match(text):
                errors.append(err(path, "INVALID_FORMAT", f"{label} must be a phone number"))
        return text
    options = {o["value"] for o in field.get("options", [])}
    if field_type in ("dropdown", "radio"):
        if str(value) not in options:
            errors.append(err(path, "INVALID_OPTION", f"{label}: pick one of {', '.join(sorted(options))}"))
            return None
        return str(value)
    if field_type == "checkbox":
        if not isinstance(value, list) or any(str(v) not in options for v in value):
            errors.append(err(path, "INVALID_OPTION", f"{label}: pick from {', '.join(sorted(options))}"))
            return None
        picked = list(dict.fromkeys(str(v) for v in value))
        if len(picked) > rules.get("max_items", MAX_OPTIONS):
            errors.append(err(path, "TOO_MANY", f"{label}: pick at most {rules['max_items']}"))
        elif len(picked) < rules.get("min_items", 0):
            errors.append(err(path, "TOO_FEW", f"{label}: pick at least {rules['min_items']}"))
        return picked
    if field_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            errors.append(err(path, "INVALID_TYPE", f"{label} must be a number"))
            return None
        try:
            number = Decimal(str(value).strip())
            if not number.is_finite():
                raise InvalidOperation
        except InvalidOperation:
            errors.append(err(path, "INVALID_TYPE", f"{label} must be a number"))
            return None
        places = max(-number.normalize().as_tuple().exponent, 0)
        decimals = rules.get("decimals", 6)
        if places > decimals:
            errors.append(err(path, "TOO_MANY_DECIMALS", f"{label} can have at most {decimals} decimal places" if decimals else f"{label} must be a whole number"))
        elif "min" in rules and number < Decimal(str(rules["min"])):
            errors.append(err(path, "TOO_SMALL", f"{label} must be at least {rules['min']}"))
        elif "max" in rules and number > Decimal(str(rules["max"])):
            errors.append(err(path, "TOO_LARGE", f"{label} must be at most {rules['max']}"))
        return int(number) if places == 0 else float(number)
    if field_type == "date":
        try:
            day = _parse_date(value)
        except ValueError:
            errors.append(err(path, "INVALID_FORMAT", f"{label} must be a date like 2026-01-31"))
            return None
        today = date.today()
        if "min" in rules and day.isoformat() < rules["min"]:
            errors.append(err(path, "TOO_SMALL", f"{label} cannot be before {rules['min']}"))
        elif "max" in rules and day.isoformat() > rules["max"]:
            errors.append(err(path, "TOO_LARGE", f"{label} cannot be after {rules['max']}"))
        elif rules.get("not_in_past") and day < today:
            errors.append(err(path, "IN_THE_PAST", f"{label} cannot be in the past"))
        elif rules.get("not_in_future") and day > today:
            errors.append(err(path, "IN_THE_FUTURE", f"{label} cannot be in the future"))
        return day.isoformat()
    if field_type == "time":
        try:
            moment = _parse_time(value).strftime("%H:%M")
        except ValueError:
            errors.append(err(path, "INVALID_FORMAT", f"{label} must be a time like 09:30"))
            return None
        if "min" in rules and moment < rules["min"]:
            errors.append(err(path, "TOO_SMALL", f"{label} cannot be before {rules['min']}"))
        elif "max" in rules and moment > rules["max"]:
            errors.append(err(path, "TOO_LARGE", f"{label} cannot be after {rules['max']}"))
        return moment
    if field_type == "toggle":
        if not isinstance(value, bool):
            errors.append(err(path, "INVALID_TYPE", f"{label} must be true or false"))
            return None
        return value
    if field_type == "datetime":
        try:
            moment = _parse_datetime(value)
        except (ValueError, TypeError):
            errors.append(err(path, "INVALID_FORMAT", f"{label} must be a date and time like 2026-01-31T09:30"))
            return None
        from datetime import datetime as _dt

        now = _dt.utcnow()
        if "min" in rules and moment.isoformat() < rules["min"]:
            errors.append(err(path, "TOO_SMALL", f"{label} cannot be before {rules['min']}"))
        elif "max" in rules and moment.isoformat() > rules["max"]:
            errors.append(err(path, "TOO_LARGE", f"{label} cannot be after {rules['max']}"))
        elif rules.get("not_in_past") and moment < now:
            errors.append(err(path, "IN_THE_PAST", f"{label} cannot be in the past"))
        elif rules.get("not_in_future") and moment > now:
            errors.append(err(path, "IN_THE_FUTURE", f"{label} cannot be in the future"))
        return moment.isoformat()
    if field_type == "reference_url":
        many = "max_items" in rules
        entries = value if isinstance(value, list) and many else [value]
        out, seen = [], set()
        for i, entry in enumerate(entries):
            where = f"{path}[{i}]" if many else path
            raw = entry.get("url") if isinstance(entry, dict) else entry
            try:
                info = inspect_url(raw)
            except UnsafeUrl as exc:
                errors.append(err(where, exc.code, f"{label}: {exc.message}"))
                continue
            if rules.get("require_https") and not info["url"].startswith("https://"):
                errors.append(err(where, "HTTPS_REQUIRED", f"{label} must start with https://"))
            elif info["provider"] not in rules.get("allowed_providers", list(ALLOWED_PROVIDERS)):
                errors.append(err(where, "PROVIDER_NOT_ALLOWED", f"{label}: links from {', '.join(rules['allowed_providers'])} only"))
            elif info["url"] in seen:
                errors.append(err(where, "DUPLICATE_ITEM", f"{label}: the same link is added twice"))
            else:
                seen.add(info["url"])
                item = {k: info[k] for k in ("url", "provider", "video_id", "thumbnail_url")}
                if isinstance(entry, dict) and entry.get("label"):
                    item["label"] = clean_text(entry["label"], 150)
                out.append(item)
        if not many:
            return out[0] if out else None
        if len(entries) > rules["max_items"]:
            errors.append(err(path, "TOO_MANY", f"{label}: at most {rules['max_items']}"))
        return out
    if field_type == "tags":
        if not isinstance(value, list):
            errors.append(err(path, "INVALID_TYPE", f"{label} must be a list of tags"))
            return None
        tags, longest = [], rules.get("max_length", 30)
        for raw in value:
            tag = clean_text(raw, 200)
            if not tag:
                continue
            if len(tag) > longest:
                errors.append(err(path, "TOO_LONG", f"A tag can have at most {longest} characters"))
                return None
            if tag.lower() not in {t.lower() for t in tags}:
                tags.append(tag)
        if len(tags) > rules.get("max_items", 10):
            errors.append(err(path, "TOO_MANY", f"{label}: at most {rules.get('max_items', 10)}"))
        return tags
    # media: additional_images, supporting_document, video. One item or a list of items.
    items = value if isinstance(value, list) else [value]
    cleaned, seen, problems_before = [], set(), len(errors)
    for i, raw in enumerate(items):
        item = _media_item(field, raw, f"{path}[{i}]", errors)
        if item is None:
            continue
        if item["url"] in seen:
            errors.append(err(f"{path}[{i}]", "DUPLICATE_ITEM", f"{label}: the same file or link is added twice"))
            continue
        seen.add(item["url"])
        cleaned.append(item)
    maximum = rules.get("max_items", DEFAULT_MAX_ITEMS[field_type])
    if len(items) > maximum:
        errors.append(err(path, "TOO_MANY", f"{label}: at most {maximum}"))
    elif len(cleaned) < rules.get("min_items", 0) and len(errors) == problems_before:
        errors.append(err(path, "TOO_FEW", f"{label}: add at least {rules['min_items']}"))
    return cleaned


TYPE_LABELS = {"short_text": "Short text", "rich_text": "Rich text", "long_text": "Long text", "dropdown": "Dropdown", "radio": "Radio buttons", "checkbox": "Checkboxes (multi-select)",
               "number": "Number", "date": "Date", "time": "Time", "reference_url": "Reference URL", "additional_images": "Additional images",
               "supporting_document": "Supporting document", "video": "Video", "tags": "Tags", "datetime": "Date and time", "toggle": "Yes / no switch"}


def describe_types(include_module_types: bool = False) -> list[dict]:
    """What a form builder screen needs to offer each type: its renderer, whether it has options, the file types it can be limited to, and its rules.
    `include_module_types` adds the Event and Training types (datetime, toggle)."""
    out = []
    for key, info in TYPES.items():
        if info.get("module") and not include_module_types:
            continue
        rules = {name: {"kind": kind, **({"min": low, "max": high} if kind in ("int", "number") and low is not None else {}),
                        **({"values": list(low)} if kind == "enum" else {})} for name, (kind, low, high) in RULES[key].items()}
        out.append({"type": key, "renderer": info["renderer"], "label": TYPE_LABELS[key], "supports_options": info["options"], "module_type": bool(info.get("module")),
                    "accepts_files": sorted(info.get("files", {})), "rules": rules})
    return out
