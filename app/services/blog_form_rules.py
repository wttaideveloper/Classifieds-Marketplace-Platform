"""What a blog / news form is made of, and how a post is checked against one. No database in here.

The form model is the shared one in `app/form_builder` (14 explicit field types, `enabled`, `visibility`, `order`, ids and stable keys). A
definition is `{"sections": [{"id", "key", "title", "description", "order", "fields": [field, ...]}]}`. Blog adds one thing to a field:
`kind`, which is `core` for the nine fields every post has (title, excerpt, content, category, tags, cover_image, additional_images,
reference_urls, supporting_documents: stored in the post's own columns) or `custom` (a value saved under its key in `custom_values`).

Title, body (content) and category are protected `system` fields: always there, shown, required and published. The other core fields can be
switched off or made optional but not deleted. `enterprise_admin_only` fields exist only on posts written by an Enterprise Admin and never
reach the public APIs.
"""
import re
import uuid

from app.core.config import settings
from app.exceptions.problem import problem
from app.form_builder import fields as common
from app.form_builder import projection
from app.form_builder.fields import err, is_empty, normalize_field, validate_value

# key -> (type, label, extra field settings)
CORE_FIELDS = {
    "title": ("short_text", "Title", {"validation": {"max_length": 150}}),
    "excerpt": ("long_text", "Summary", {"validation": {"max_length": 300}}),
    "content": ("rich_text", "Body", {"validation": {"max_length": 100_000}}),
    "category": ("dropdown", "Category", {"options_source": "blog_categories"}),
    "tags": ("tags", "Tags", {"validation": {"max_items": 10, "max_length": 30}}),
    "cover_image": ("additional_images", "Cover image", {"validation": {"max_items": 1}}),
    "additional_images": ("additional_images", "Additional images", {}),
    "reference_urls": ("reference_url", "Reference links", {}),
    "supporting_documents": ("supporting_document", "Supporting documents", {}),
}
ALWAYS_REQUIRED = ("title", "content", "category")             # the protected system fields
LEGACY_CORE_TYPES = ("text", "richtext", "category", "tags", "image", "images", "urls", "documents")   # what the core fields were called before the shared form model
LISTED = ("additional_images", "reference_urls", "supporting_documents")   # core fields that may be enterprise_admin_only
KEY_PATTERN = common.KEY_PATTERN
MAX_SECTIONS, MAX_FIELDS = 20, 60
SHORT_LABEL = {"content": "body", "category": "category", "title": "title"}
NAMESPACE = uuid.UUID("5b3f1a7e-9d2c-4c4e-8a61-0f2d7b9c1e44")

CORE_LIMITS = {
    "title": {"min_length": (1, 150), "max_length": (1, 150)},
    "excerpt": {"min_length": (0, 300), "max_length": (1, 300)},
    "content": {"min_length": (0, 100_000), "max_length": (1, 100_000)},
    "tags": {"max_items": (1, 10), "max_length": (1, 30)},
    "cover_image": {"max_items": (1, 1)},
}


def _item_limits() -> dict:
    return {
        "additional_images": {"max_items": (1, settings.BLOG_MAX_ADDITIONAL_IMAGES)},
        "reference_urls": {"max_items": (1, settings.BLOG_MAX_REFERENCE_URLS)},
        "supporting_documents": {"max_items": (1, settings.BLOG_MAX_DOCUMENTS)},
    }


plain = lambda value: re.sub(r"<[^>]*>", "", value or "").strip()  # noqa: E731


def invalid(errors: list[dict], detail: str | None = None, code: str = "VALIDATION_FAILED"):
    """A 422 listing every problem. With one problem, `detail` is that problem's sentence."""
    if detail is None:
        detail = errors[0]["message"] if len(errors) == 1 else "Some fields need attention"
    return problem(422, detail, code, errors=errors)


def _uid(*parts: str) -> str:
    return str(uuid.uuid5(NAMESPACE, ":".join(parts)))


# ---------------------------------------------------------------- the built-in form

def _core(key: str, **over) -> dict:
    ftype, label, extra = CORE_FIELDS[key]
    must = key in ALWAYS_REQUIRED
    field = {"id": _uid("field", key), "key": key, "kind": "core", "type": ftype, "renderer": common.renderer_of(ftype), "label": label, "placeholder": None,
             "help_text": None, "required": must, "enabled": True, "order": 0, "validation": {}, "options": [], "visibility": "published", "system": must}
    field.update({k: (dict(v) if isinstance(v, dict) else v) for k, v in extra.items()})
    field.update(over)
    return field


def _section(key: str, title: str, fields: list) -> dict:
    return {"id": _uid("section", key), "key": key, "title": title, "description": None, "order": 0, "visibility": "published", "enabled": True, "fields": fields}


def _numbered(definition: dict) -> dict:
    for i, section in enumerate(definition["sections"], start=1):
        section["order"] = i
        for j, field in enumerate(section["fields"], start=1):
            field["order"] = j
    return definition


def standard_definition() -> dict:
    return _numbered({"sections": [
        _section("basics", "Basics", [_core("title"), _core("category"), _core("excerpt"), _core("tags")]),
        _section("content", "Content", [_core("content")]),
        _section("media", "Images", [_core("cover_image"), _core("additional_images")]),
        _section("references", "References", [_core("reference_urls"), _core("supporting_documents")]),
    ]})


# ---------------------------------------------------------------- checking a definition the Super Admin sent

def _legacy(raw: dict) -> dict:
    """Older definitions (before the shared form model) used other names. They are turned into the explicit types here, once."""
    raw = dict(raw)
    kind = str(raw.get("type") or "").lower()
    rules = dict(raw.get("validation") or {})
    if kind == "text" and (rules.get("max_length") or 0) > 500:
        raw["type"] = "long_text"
    elif kind == "email":
        raw["type"], rules = "short_text", {**rules, "format": "email"}
    elif kind == "image":
        raw["type"], rules = "additional_images", {**rules, "max_items": 1}
    elif kind == "media_upload":
        raw["type"] = "additional_images"
    elif kind == "checkbox" and not raw.get("options"):         # the old single yes/no box
        raw["type"], raw["options"] = "radio", [{"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}]
    elif kind == "number" and rules.pop("integer", None):
        rules["decimals"] = 0
    raw["validation"] = rules
    return raw


def _field(raw, path: str, errors: list, taken: set) -> dict | None:
    if not isinstance(raw, dict):
        errors.append(err(path, "INVALID", "A field must be an object"))
        return None
    key = str(raw.get("key") or "").strip()
    if key in taken:
        errors.append(err(f"{path}.key", "DUPLICATE", f"The field '{key}' is in the form twice"))
        return None
    raw = _legacy(raw)
    if key in CORE_FIELDS:
        taken.add(key)
        ftype, label, extra = CORE_FIELDS[key]
        if raw.get("type") not in (None, ftype) and common.canonical_type(raw.get("type")) != ftype and str(raw.get("type")).lower() not in LEGACY_CORE_TYPES:
            errors.append(err(f"{path}.type", "FIXED", f"The type of {key} cannot be changed"))
        must = key in ALWAYS_REQUIRED
        shown = dict(raw, key=key, type=ftype, label=raw.get("label") or label, id=raw.get("id") or _uid("field", key))
        shown["validation"] = _core_rules(key, raw.get("validation"), path, errors)
        if "options_source" in extra:
            shown["options_source"] = extra["options_source"]
        field = normalize_field(shown, path=path, errors=errors, system=must)
        if field is None:
            return None
        field["kind"] = "core"
        wants_internal = common.canonical_visibility(raw.get("visibility"), legacy_enterprise_only=bool(raw.get("enterprise_only"))) == "enterprise_admin_only"
        if must and (raw.get("enabled", raw.get("visible", True)) is False or raw.get("required") is False or wants_internal):
            errors.append(err(path, "ALWAYS_REQUIRED", f"{key} is always shown, required and published"))
        if wants_internal and key not in LISTED and not must:
            errors.append(err(f"{path}.visibility", "ALWAYS_PUBLIC", f"{key} is always published. Only extra images, links, documents and custom fields can be Enterprise Admin only"))
        if must:
            field["enabled"], field["required"], field["visibility"] = True, True, "published"
        elif key not in LISTED:
            field["visibility"] = "published"
        return field
    if not KEY_PATTERN.match(key):
        errors.append(err(f"{path}.key", "INVALID_KEY", "A key starts with a letter and has 2 to 40 lowercase letters, digits or underscores"))
        return None
    taken.add(key)
    raw["id"] = raw.get("id") or _uid("field", key)
    field = normalize_field(raw, path=path, errors=errors)
    if field is not None:
        field["kind"] = "custom"
    return field


def _core_rules(key: str, raw, path: str, errors: list) -> dict:
    spec = {**CORE_LIMITS, **_item_limits()}.get(key, {})
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
    sections, taken, section_keys, section_titles, total = [], set(), set(), set(), 0
    for i, sec in enumerate(sections_raw[:MAX_SECTIONS]):
        path = f"definition.sections[{i}]"
        if not isinstance(sec, dict):
            errors.append(err(path, "INVALID", "A section must be an object"))
            continue
        title = common.clean_text(sec.get("title"), 120)
        key = common.clean_text(sec.get("key"), 40) or f"section_{i + 1}"
        if not title:
            errors.append(err(f"{path}.title", "REQUIRED", "A section needs a title"))
        if key in section_keys:
            errors.append(err(f"{path}.key", "DUPLICATE", f"The section '{key}' is in the form twice"))
        elif (title or "").lower() in section_titles:
            errors.append(err(f"{path}.title", "DUPLICATE_SECTION", f"There are two sections titled '{title}'"))
        section_keys.add(key)
        section_titles.add((title or "").lower())
        fields, labels = [], set()
        for j, raw_field in enumerate(sec.get("fields") or []):
            total += 1
            if total > MAX_FIELDS:
                errors.append(err(path, "TOO_MANY", f"At most {MAX_FIELDS} fields in a form"))
                break
            field = _field(raw_field, f"{path}.fields[{j}]", errors, taken)
            if field:
                if field["label"].lower() in labels:
                    errors.append(err(f"{path}.fields[{j}].label", "DUPLICATE_LABEL", f"Two fields in this section are labelled '{field['label']}'"))
                labels.add(field["label"].lower())
                fields.append(field)
        visibility = str(sec.get("visibility") or "published").lower()
        if visibility not in ("published", "enterprise_admin_only"):
            errors.append(err(f"{path}.visibility", "INVALID", "visibility must be published or enterprise_admin_only"))
            visibility = "published"
        sections.append({"id": str(sec.get("id") or _uid("section", key)), "key": key, "title": title or key, "description": common.clean_text(sec.get("description"), 300),
                         "order": 0, "visibility": visibility, "enabled": bool(sec.get("enabled", True)), "fields": fields})
    for must in ALWAYS_REQUIRED:
        if must not in taken:
            errors.append(err("definition", "MISSING_CORE_FIELD", f"The form must include the {must} field"))
    if errors:
        raise invalid(errors, "The form has problems", "INVALID_FORM")
    return _numbered({"sections": sections})


def _same_layout(raw) -> bool:
    """True when what was sent is the built-in layout (so a client may send back what it was given)."""
    try:
        return normalize_definition(raw, mode="configurable") == standard_definition()
    except Exception:
        return False


def is_current_format(definition: dict | None) -> bool:
    fields = [f for s in (definition or {}).get("sections", []) for f in s.get("fields", [])]
    return bool(fields) and all("enabled" in f and "renderer" in f and "kind" in f for f in fields) and all("id" in s for s in definition["sections"])


def upgrade_definition(definition: dict | None, *, mode: str = "configurable") -> dict | None:
    """A stored definition in the current format. One already in it is returned as it is; an older one is converted (same result every time:
    ids come from keys). A definition that cannot be converted is returned unchanged rather than lost."""
    if not definition or is_current_format(definition):
        return definition
    try:
        return normalize_definition(definition, mode=mode)
    except Exception:
        return definition


def fields_of(definition: dict) -> list[dict]:
    """Every field. A field in a section that is `enterprise_admin_only` or switched off is, for every check, `enterprise_admin_only` or switched off itself."""
    out = []
    for section in (definition or standard_definition()).get("sections", []):
        private, off = section.get("visibility", "published") != "published", section.get("enabled", True) is False
        for field in section.get("fields", []):
            out.append({**field, **({"visibility": "enterprise_admin_only"} if private else {}), **({"enabled": False, "required": False} if off else {})})
    return out


# ---------------------------------------------------------------- checking a post against a definition

def applies(field: dict, enterprise_post: bool) -> bool:
    """A switched-off field is not offered; an Enterprise Admin only field is only offered on posts an Enterprise Admin writes."""
    return bool(field.get("enabled", True)) and (enterprise_post or field.get("visibility", "published") == "published")


def clean_custom_value(field: dict, value, path: str, errors: list):
    """The stored form of one custom value, or None after adding to `errors`. Empty values are dropped by the caller."""
    from app.utils.html_sanitize import clean_html

    return validate_value(field, value, path, errors, sanitize_html=clean_html)


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
        elif is_empty(value):
            out.append(err(field["key"] if field["kind"] == "core" else f"custom_fields.{field['key']}", "REQUIRED", f"{field['label']} is required"))
    return out


def public_custom(definition: dict, custom: dict | None) -> list[dict]:
    """The custom values the public may read, in form order, with their labels and explicit types. Never an Enterprise Admin only value."""
    custom_keys = {f["key"] for f in fields_of(definition) if f["kind"] == "custom"}
    return [v for v in projection.project_values(definition, {k: x for k, x in (custom or {}).items() if k in custom_keys}, "public")]


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
