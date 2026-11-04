"""What a blog / news form is made of, and how a post is checked against one. No database in here.

A form definition is `{"sections": [{"key", "title", "description", "fields": [field, ...]}]}`. A field is either one of the nine
core fields of a post (title, excerpt, content, category, tags, cover_image, additional_images, reference_urls,
supporting_documents), whose label, requiredness, visibility, help text and limits can be set, or a custom field the Super Admin adds
(text, textarea, number, date, select, multiselect, checkbox, url, email). Title, body and category are always shown and required.
Position in the lists is the order.
"""
import re
from datetime import date
from urllib.parse import urlparse

from app.core.config import settings
from app.exceptions.problem import problem

CORE_FIELDS = {
    "title": ("text", "Title"),
    "excerpt": ("text", "Summary"),
    "content": ("richtext", "Body"),
    "category": ("category", "Category"),
    "tags": ("tags", "Tags"),
    "cover_image": ("image", "Cover image"),
    "additional_images": ("images", "Additional images"),
    "reference_urls": ("urls", "Reference links"),
    "supporting_documents": ("documents", "Supporting documents"),
}
ALWAYS_REQUIRED = ("title", "content", "category")
CUSTOM_TYPES = ("text", "textarea", "number", "date", "select", "multiselect", "checkbox", "url", "email", "image", "media_upload")
MEDIA_TYPES = ("image", "media_upload")        # image: one picture; media_upload: one or several (a URL, or a list of URLs)
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
MAX_SECTIONS, MAX_FIELDS, MAX_OPTIONS = 20, 60, 50
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# what the notices and messages call a core field
SHORT_LABEL = {"content": "body", "category": "category", "title": "title"}

CORE_LIMITS = {
    "title": {"min_length": (1, 150), "max_length": (1, 150)},
    "excerpt": {"min_length": (0, 300), "max_length": (1, 300)},
    "content": {"min_length": (0, 100_000), "max_length": (1, 100_000)},
    "tags": {"max_items": (1, 10)},
}


def _item_limits() -> dict:
    return {
        "additional_images": {"max_items": (1, settings.BLOG_MAX_ADDITIONAL_IMAGES)},
        "reference_urls": {"max_items": (1, settings.BLOG_MAX_REFERENCE_URLS)},
        "supporting_documents": {"max_items": (1, settings.BLOG_MAX_DOCUMENTS)},
    }


def plain(value: str | None) -> str:
    return re.sub(r"<[^>]*>", "", value or "").strip()


def is_empty(value) -> bool:
    return value is None or value == "" or value == [] or value == {}


def err(field: str, code: str, message: str) -> dict:
    return {"field": field, "code": code, "message": message}


def invalid(errors: list[dict], detail: str | None = None, code: str = "VALIDATION_FAILED"):
    """A 422 listing every problem. With one problem, `detail` is that problem's sentence."""
    if detail is None:
        detail = errors[0]["message"] if len(errors) == 1 else "Some fields need attention"
    return problem(422, detail, code, errors=errors)


# ---------------------------------------------------------------- the built-in form

def _blank(key: str, **over) -> dict:
    field = {"key": key, "kind": "core", "type": "text", "label": key, "required": False, "visible": True, "visibility": "public",
             "enterprise_only": False, "help_text": None, "placeholder": None, "options": [], "validation": {}}
    field.update(over)
    return field


def _core(key: str, **over) -> dict:
    kind, label = CORE_FIELDS[key]
    return _blank(key, **{"type": kind, "label": label, "required": key in ALWAYS_REQUIRED, **over})


def standard_definition() -> dict:
    return {"sections": [
        {"key": "basics", "title": "Basics", "description": None, "fields": [_core("title"), _core("category"), _core("excerpt"), _core("tags")]},
        {"key": "content", "title": "Content", "description": None, "fields": [_core("content")]},
        {"key": "media", "title": "Images", "description": None, "fields": [_core("cover_image"), _core("additional_images")]},
        {"key": "references", "title": "References", "description": None, "fields": [_core("reference_urls"), _core("supporting_documents")]},
    ]}


# ---------------------------------------------------------------- checking a definition the Super Admin sent

def _text(value, limit: int) -> str | None:
    text = " ".join(str(value).split()) if value is not None else ""
    return text[:limit] or None


def _limits(raw, spec: dict, path: str, errors: list) -> dict:
    out = {}
    for name, value in (raw or {}).items():
        if name not in spec:
            errors.append(err(f"{path}.validation.{name}", "UNKNOWN_RULE", f"'{name}' is not a rule of this field"))
            continue
        low, high = spec[name]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            errors.append(err(f"{path}.validation.{name}", "INVALID_RULE", f"{name} must be a whole number from {low} to {high}"))
            continue
        out[name] = value
    if "min_length" in out and "max_length" in out and out["min_length"] > out["max_length"]:
        errors.append(err(f"{path}.validation.min_length", "INVALID_RULE", "min_length cannot be more than max_length"))
    return out


def _custom_rules(ftype: str, raw, path: str, errors: list) -> dict:
    raw = dict(raw or {})
    if ftype in ("text", "textarea"):
        out = _limits({k: raw.pop(k) for k in ("min_length", "max_length") if k in raw}, {"min_length": (0, 5000), "max_length": (1, 5000)}, path, errors)
        if raw.get("pattern") is not None:
            pattern = str(raw.pop("pattern"))
            try:
                re.compile(pattern)
                if len(pattern) > 200:
                    raise re.error("too long")
                out["pattern"] = pattern
            except re.error:
                errors.append(err(f"{path}.validation.pattern", "INVALID_RULE", "pattern must be a valid regular expression of at most 200 characters"))
    elif ftype == "number":
        out = {}
        for name in ("min", "max"):
            if name in raw:
                value = raw.pop(name)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    errors.append(err(f"{path}.validation.{name}", "INVALID_RULE", f"{name} must be a number"))
                else:
                    out[name] = value
        if "integer" in raw:
            out["integer"] = bool(raw.pop("integer"))
        if "min" in out and "max" in out and out["min"] > out["max"]:
            errors.append(err(f"{path}.validation.min", "INVALID_RULE", "min cannot be more than max"))
    elif ftype == "date":
        out = {}
        for name in ("min", "max"):
            if name in raw:
                try:
                    out[name] = date.fromisoformat(str(raw.pop(name))).isoformat()
                except ValueError:
                    errors.append(err(f"{path}.validation.{name}", "INVALID_RULE", f"{name} must be a date like 2026-01-31"))
    elif ftype == "multiselect":
        out = _limits({k: raw.pop(k) for k in ("max_items",) if k in raw}, {"max_items": (1, MAX_OPTIONS)}, path, errors)
    elif ftype == "media_upload":
        out = _limits({k: raw.pop(k) for k in ("max_items",) if k in raw}, {"max_items": (1, settings.BLOG_MAX_ADDITIONAL_IMAGES)}, path, errors)
    else:
        out = {}
    for left in raw:
        errors.append(err(f"{path}.validation.{left}", "UNKNOWN_RULE", f"'{left}' is not a rule of a {ftype} field"))
    return out


def _options(raw, path: str, errors: list) -> list[dict]:
    out, seen = [], set()
    if not isinstance(raw, list) or not raw:
        errors.append(err(f"{path}.options", "REQUIRED", "Add at least one option"))
        return out
    if len(raw) > MAX_OPTIONS:
        errors.append(err(f"{path}.options", "TOO_MANY", f"At most {MAX_OPTIONS} options"))
    for i, item in enumerate(raw[:MAX_OPTIONS]):
        value = _text(item.get("value") if isinstance(item, dict) else item, 100)
        label = _text(item.get("label") if isinstance(item, dict) else item, 120) or value
        if not value:
            errors.append(err(f"{path}.options[{i}]", "REQUIRED", "An option needs a value"))
        elif value.lower() in seen:
            errors.append(err(f"{path}.options[{i}]", "DUPLICATE", f"The option '{value}' is listed twice"))
        else:
            seen.add(value.lower())
            out.append({"value": value, "label": label})
    return out


def _field(raw: dict, path: str, errors: list, taken: set) -> dict | None:
    if not isinstance(raw, dict):
        errors.append(err(path, "INVALID", "A field must be an object"))
        return None
    key = str(raw.get("key") or "").strip()
    if key in taken:
        errors.append(err(f"{path}.key", "DUPLICATE", f"The field '{key}' is in the form twice"))
        return None
    if key in CORE_FIELDS:
        taken.add(key)
        ftype, label = CORE_FIELDS[key]
        field = _core(key, label=_text(raw.get("label"), 120) or label)
        spec = {**CORE_LIMITS, **_item_limits()}.get(key, {})
        field["validation"] = _limits(raw.get("validation"), spec, path, errors)
        if raw.get("type") not in (None, ftype):
            errors.append(err(f"{path}.type", "FIXED", f"The type of {key} cannot be changed"))
        must = key in ALWAYS_REQUIRED
        if must and (raw.get("visible") is False or raw.get("required") is False or raw.get("visibility") == "internal" or raw.get("enterprise_only")):
            errors.append(err(path, "ALWAYS_REQUIRED", f"{key} is always shown, required and public"))
        field["visible"] = True if must else bool(raw.get("visible", True))
        field["required"] = True if must else bool(raw.get("required", False)) and field["visible"]
        listed = key in ("additional_images", "reference_urls", "supporting_documents")
        if not listed and raw.get("visibility") == "internal":
            errors.append(err(f"{path}.visibility", "ALWAYS_PUBLIC", f"{key} is always public. Only extra images, links, documents and custom fields can be internal"))
        field["visibility"] = raw.get("visibility", "public") if listed and not must else "public"
        field["enterprise_only"] = False if must else bool(raw.get("enterprise_only", False))
    else:
        if not KEY_PATTERN.match(key):
            errors.append(err(f"{path}.key", "INVALID_KEY", "A key starts with a letter and has 2 to 40 lowercase letters, digits or underscores"))
            return None
        taken.add(key)
        ftype = str(raw.get("type") or "")
        if ftype not in CUSTOM_TYPES:
            errors.append(err(f"{path}.type", "INVALID_TYPE", f"type must be one of: {', '.join(CUSTOM_TYPES)}"))
            return None
        label = _text(raw.get("label"), 120)
        if not label:
            errors.append(err(f"{path}.label", "REQUIRED", "A field needs a label"))
            return None
        field = _blank(key, kind="custom", type=ftype, label=label)
        field["visible"] = bool(raw.get("visible", True))
        field["required"] = bool(raw.get("required", False)) and field["visible"]
        field["visibility"] = raw.get("visibility", "public")
        field["enterprise_only"] = bool(raw.get("enterprise_only", False))
        field["validation"] = _custom_rules(ftype, raw.get("validation"), path, errors)
        if ftype in ("select", "multiselect"):
            field["options"] = _options(raw.get("options"), path, errors)
    if field["visibility"] not in ("public", "internal"):
        errors.append(err(f"{path}.visibility", "INVALID", "visibility must be public or internal"))
        field["visibility"] = "public"
    field["help_text"] = _text(raw.get("help_text"), 300)
    field["placeholder"] = _text(raw.get("placeholder"), 120)
    return field


def normalize_definition(raw, *, mode: str) -> dict:
    """The cleaned definition, or a 422 listing every problem. A Standard form is always the built-in layout."""
    if mode == "standard":
        if raw not in (None, {}) and _same_layout(raw):
            return standard_definition()
        if raw not in (None, {}):
            raise invalid([err("definition", "STANDARD_IS_FIXED", "A Standard form cannot be changed. Use mode 'configurable' to change fields")],
                          "A Standard form uses the built-in fields", "STANDARD_FORM_FIXED")
        return standard_definition()
    errors: list[dict] = []
    sections_raw = (raw or {}).get("sections") if isinstance(raw, dict) else None
    if not isinstance(sections_raw, list) or not sections_raw:
        raise invalid([err("definition.sections", "REQUIRED", "Add at least one section")], "The form has no sections", "INVALID_FORM")
    if len(sections_raw) > MAX_SECTIONS:
        errors.append(err("definition.sections", "TOO_MANY", f"At most {MAX_SECTIONS} sections"))
    sections, taken, section_keys, total = [], set(), set(), 0
    for i, sec in enumerate(sections_raw[:MAX_SECTIONS]):
        path = f"definition.sections[{i}]"
        if not isinstance(sec, dict):
            errors.append(err(path, "INVALID", "A section must be an object"))
            continue
        title = _text(sec.get("title"), 120)
        key = _text(sec.get("key"), 40) or f"section_{i + 1}"
        if not title:
            errors.append(err(f"{path}.title", "REQUIRED", "A section needs a title"))
        if key in section_keys:
            errors.append(err(f"{path}.key", "DUPLICATE", f"The section '{key}' is in the form twice"))
        section_keys.add(key)
        fields = []
        for j, raw_field in enumerate(sec.get("fields") or []):
            total += 1
            if total > MAX_FIELDS:
                errors.append(err(path, "TOO_MANY", f"At most {MAX_FIELDS} fields in a form"))
                break
            field = _field(raw_field, f"{path}.fields[{j}]", errors, taken)
            if field:
                fields.append(field)
        sections.append({"key": key, "title": title or key, "description": _text(sec.get("description"), 300), "fields": fields})
    for must in ALWAYS_REQUIRED:
        if must not in taken:
            errors.append(err("definition", "MISSING_CORE_FIELD", f"The form must include the {must} field"))
    if errors:
        raise invalid(errors, "The form has problems", "INVALID_FORM")
    return {"sections": sections}


def _same_layout(raw) -> bool:
    """True when what was sent is the built-in layout (so a client may send back what it was given)."""
    try:
        return normalize_definition(raw, mode="configurable") == standard_definition()
    except Exception:
        return False


def fields_of(definition: dict) -> list[dict]:
    return [f for s in (definition or standard_definition()).get("sections", []) for f in s.get("fields", [])]


# ---------------------------------------------------------------- checking a post against a definition

def applies(field: dict, enterprise_post: bool) -> bool:
    return bool(field.get("visible", True)) and (enterprise_post or not field.get("enterprise_only"))


def _check_http(value, *, allow_relative: bool) -> str | None:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if not url or len(url) > 2000 or url.startswith("//"):
        return None
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return url
    return url if allow_relative and parsed.scheme == "" and url.startswith("/") else None


def clean_custom_value(field: dict, value, path: str, errors: list):
    """The stored form of one custom value, or None after adding to `errors`. Empty values are dropped by the caller."""
    ftype, rules = field["type"], field.get("validation") or {}
    label = field["label"]
    if ftype in ("text", "textarea"):
        text = str(value).strip() if isinstance(value, (str, int, float)) and not isinstance(value, bool) else None
        if text is None:
            errors.append(err(path, "INVALID_TYPE", f"{label} must be text"))
            return None
        limit = rules.get("max_length", 500 if ftype == "text" else 5000)
        if len(text) > limit:
            errors.append(err(path, "TOO_LONG", f"{label} can have at most {limit} characters"))
        elif len(text) < rules.get("min_length", 0):
            errors.append(err(path, "TOO_SHORT", f"{label} needs at least {rules['min_length']} characters"))
        elif rules.get("pattern") and not re.search(rules["pattern"], text):
            errors.append(err(path, "INVALID_FORMAT", f"{label} is not in the expected format"))
        return text
    if ftype == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(err(path, "INVALID_TYPE", f"{label} must be a number"))
            return None
        if rules.get("integer") and value != int(value):
            errors.append(err(path, "INVALID_FORMAT", f"{label} must be a whole number"))
        elif "min" in rules and value < rules["min"]:
            errors.append(err(path, "TOO_SMALL", f"{label} must be at least {rules['min']}"))
        elif "max" in rules and value > rules["max"]:
            errors.append(err(path, "TOO_LARGE", f"{label} must be at most {rules['max']}"))
        return value
    if ftype == "date":
        try:
            day = date.fromisoformat(str(value))
        except ValueError:
            errors.append(err(path, "INVALID_FORMAT", f"{label} must be a date like 2026-01-31"))
            return None
        if "min" in rules and day.isoformat() < rules["min"]:
            errors.append(err(path, "TOO_SMALL", f"{label} cannot be before {rules['min']}"))
        elif "max" in rules and day.isoformat() > rules["max"]:
            errors.append(err(path, "TOO_LARGE", f"{label} cannot be after {rules['max']}"))
        return day.isoformat()
    values = {o["value"] for o in field.get("options", [])}
    if ftype == "select":
        if str(value) not in values:
            errors.append(err(path, "INVALID_OPTION", f"{label}: pick one of {', '.join(sorted(values))}"))
            return None
        return str(value)
    if ftype == "multiselect":
        if not isinstance(value, list) or any(str(v) not in values for v in value):
            errors.append(err(path, "INVALID_OPTION", f"{label}: pick from {', '.join(sorted(values))}"))
            return None
        picked = list(dict.fromkeys(str(v) for v in value))
        if "max_items" in rules and len(picked) > rules["max_items"]:
            errors.append(err(path, "TOO_MANY", f"{label}: pick at most {rules['max_items']}"))
        return picked
    if ftype == "checkbox":
        if not isinstance(value, bool):
            errors.append(err(path, "INVALID_TYPE", f"{label} must be true or false"))
            return None
        return value
    if ftype == "url":
        url = _check_http(value, allow_relative=False)
        if url is None:
            errors.append(err(path, "INVALID_FORMAT", f"{label} must be a web address starting with http:// or https://"))
        return url
    if ftype in MEDIA_TYPES:
        many = ftype == "media_upload" and isinstance(value, list)
        if isinstance(value, list) and ftype == "image":
            errors.append(err(path, "INVALID_TYPE", f"{label} takes one picture. Use a media_upload field for several"))
            return None
        items = value if many else [value]
        urls = [_check_http(v, allow_relative=True) if isinstance(v, str) else None for v in items]
        if any(u is None for u in urls):
            errors.append(err(path, "INVALID_FORMAT", f"{label} must be the web address of an uploaded file" + (", or a list of them" if ftype == "media_upload" else "")))
            return None
        urls = list(dict.fromkeys(urls))
        limit = rules.get("max_items", settings.BLOG_MAX_ADDITIONAL_IMAGES)
        if len(urls) > limit:
            errors.append(err(path, "TOO_MANY", f"{label}: at most {limit}"))
        return urls if many else urls[0]
    if ftype == "email":
        text = str(value).strip()
        if not EMAIL.match(text) or len(text) > 254:
            errors.append(err(path, "INVALID_FORMAT", f"{label} must be an email address"))
            return None
        return text
    return None


def clean_custom(definition: dict, incoming, *, enterprise_post: bool, current: dict | None = None) -> tuple[dict, list[dict]]:
    """`incoming` laid over `current`, checked against the form. A key the form does not offer to this author is refused."""
    errors: list[dict] = []
    fields = {f["key"]: f for f in fields_of(definition) if f["kind"] == "custom"}
    result = dict(current or {})
    if incoming is None:
        return result, errors
    if not isinstance(incoming, dict):
        return result, [err("custom_fields", "INVALID_TYPE", "custom_fields must be an object of field key to value")]
    for key, value in incoming.items():
        path = f"custom_fields.{key}"
        field = fields.get(key)
        if field is None or not applies(field, enterprise_post):
            errors.append(err(path, "UNKNOWN_FIELD", f"This form has no field '{key}'"))
        elif is_empty(value):
            result.pop(key, None)
        else:
            cleaned = clean_custom_value(field, value, path, errors)
            if cleaned is not None:
                result[key] = cleaned
    return result, errors


def check_core(definition: dict, core: dict, *, enterprise_post: bool) -> list[dict]:
    """Limits and availability of the core fields. `core` maps field key to its value (category as its id)."""
    errors = []
    by_key = {f["key"]: f for f in fields_of(definition) if f["kind"] == "core"}
    for key, value in core.items():
        if is_empty(value):
            continue
        field = by_key.get(key)
        if field is None or not applies(field, enterprise_post):
            errors.append(err(key, "NOT_IN_FORM", f"This form does not use {CORE_FIELDS[key][1].lower()}"))
            continue
        rules = field.get("validation") or {}
        label = field["label"]
        if isinstance(value, str):
            size = len(plain(value)) if key == "content" else len(value)
            if "max_length" in rules and size > rules["max_length"]:
                errors.append(err(key, "TOO_LONG", f"{label} can have at most {rules['max_length']} characters"))
            if "min_length" in rules and size < rules["min_length"]:
                errors.append(err(key, "TOO_SHORT", f"{label} needs at least {rules['min_length']} characters"))
        if "max_items" in rules and isinstance(value, list) and len(value) > rules["max_items"]:
            errors.append(err(key, "TOO_MANY", f"{label}: at most {rules['max_items']}"))
    return errors


def missing_required(definition: dict, core: dict, custom: dict, *, enterprise_post: bool) -> list[dict]:
    out = []
    for field in fields_of(definition):
        if not field.get("required") or not applies(field, enterprise_post):
            continue
        value = core.get(field["key"]) if field["kind"] == "core" else (custom or {}).get(field["key"])
        if field["key"] == "content":
            if not plain(value) and "<img" not in (value or ""):
                out.append(err("content", "REQUIRED", "The body is required"))
        elif is_empty(value) or (field["type"] == "checkbox" and value is not True):
            out.append(err(field["key"] if field["kind"] == "core" else f"custom_fields.{field['key']}", "REQUIRED", f"{field['label']} is required"))
    return out


def public_custom(definition: dict, custom: dict | None) -> list[dict]:
    """The custom values the public may read, in form order, with their labels."""
    out = []
    for field in fields_of(definition):
        if field["kind"] != "custom" or field.get("visibility") != "public" or not field.get("visible", True):
            continue
        value = (custom or {}).get(field["key"])
        if not is_empty(value):
            out.append({"key": field["key"], "label": field["label"], "type": field["type"], "value": value})
    return out


def recheck(definition: dict, core: dict, custom: dict | None, *, enterprise_post: bool) -> list[dict]:
    """Everything a post must satisfy to be sent or published: what is stored is checked again against the form."""
    offered = {f["key"] for f in fields_of(definition) if applies(f, enterprise_post)}
    errors = check_core(definition, {k: v for k, v in core.items() if k in offered}, enterprise_post=enterprise_post)
    for field in fields_of(definition):
        if field["kind"] == "custom" and applies(field, enterprise_post) and not is_empty((custom or {}).get(field["key"])):
            clean_custom_value(field, custom[field["key"]], f"custom_fields.{field['key']}", errors)
    return errors + missing_required(definition, core, custom or {}, enterprise_post=enterprise_post)


def names_of_missing(definition: dict, errors: list[dict]) -> list[str]:
    """The words for "Add the ... before you ...": body, category, and the labels of other fields."""
    by_key = {f["key"]: f for f in fields_of(definition)}
    out = []
    for e in errors:
        if e["code"] != "REQUIRED":
            continue
        key = e["field"].removeprefix("custom_fields.")
        out.append(SHORT_LABEL.get(key) or (by_key[key]["label"].lower() if key in by_key else key))
    return out
