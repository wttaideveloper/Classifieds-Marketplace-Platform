"""Event and Training forms in the common form contract (read side).

The Event and Training builders store `sections` of fields in their own shape: a section has `stable_key`, `label`, `position`, `is_enabled`; a field has
`source` (`core` = one of the module's built-in fields named by `core_key`, or `custom`), `renderer`, `value_type`, `position`, `is_enabled`, and for some core
fields a `composite_config`. This module turns that stored shape into the common definition (see fields.py) **without changing what is stored**, so the
shared projections (public / enterprise_admin / super_admin) and the explicit field types can be used for them already.

Type of a field in the common contract:
  - a core field takes its type from its key where the key says what it is (tags, images, videos, documents, times) and otherwise from its renderer;
  - a custom field takes its type from its renderer (text is `short_text`, textarea `long_text`, select `dropdown`, multi_select `checkbox`, url `reference_url`,
    number, date, image `additional_images`, document `supporting_document`, video);
  - what has no counterpart among the 14 common types keeps an explicit **module type**, flagged `module_type: true`: `datetime`, `toggle` (a yes/no switch),
    `venue`, `sessions`, `ticket_types`, `registration_fields` and `json`. They are never turned into text.
Protected (`system`) fields are the module's domain-required core fields (Event: title, description, category, start_date, end_date, pricing_type; Training: title, category).
Older forms have no visibility setting, so every field is `published`; a stored `visibility` (or `is_internal`) is honoured when a form has one.
"""
from app.form_builder.fields import RULES, TYPES, renderer_of

MODULE_TYPES = {
    "datetime": "datetime_picker", "toggle": "switch", "venue": "venue_editor", "sessions": "session_list", "ticket_types": "ticket_type_list",
    "registration_fields": "registration_field_list", "json": "json_editor",
}
KEY_TYPES = {"tags": "tags", "primary_image": "additional_images", "instructor_photo": "additional_images", "gallery_images": "additional_images", "videos": "video",
             "promotional_video": "video", "documents": "supporting_document", "start_time": "time", "end_time": "time"}
SINGLE_VALUE_KEYS = {"primary_image": 1, "instructor_photo": 1, "promotional_video": 1}
RENDERER_TYPES = {"text": "short_text", "textarea": "long_text", "number": "number", "url": "reference_url", "date": "date", "time": "time", "select": "dropdown",
                  "multi_select": "checkbox", "checkbox": "toggle", "datetime": "datetime", "image": "additional_images", "document": "supporting_document", "video": "video",
                  "tags": "tags", "venue": "venue", "sessions": "sessions", "ticket_types": "ticket_types", "registration_fields": "registration_fields", "json": "json",
                  "media": "additional_images"}


def type_of(field: dict, registry: dict) -> str:
    explicit = field.get("field_type")                      # set on fields created or typed through the common builder
    if explicit in TYPES:
        return explicit
    key = field.get("core_key") if field.get("source") == "core" else None
    if key in KEY_TYPES:
        return KEY_TYPES[key]
    if key and (registry.get(key) or {}).get("value_source") and (field.get("renderer") or "text") in ("text", "select"):
        return "dropdown"                                    # a built-in field whose value is picked from a list (categories, locations, currencies ...)
    return RENDERER_TYPES.get(field.get("renderer") or "text", "short_text")


def _visibility(field: dict) -> str:
    value = str(field.get("visibility") or "").lower()
    if value in ("enterprise_admin_only", "internal") or field.get("is_internal"):
        return "enterprise_admin_only"
    return "published"


def field_to_common(field: dict, registry: dict, domain_required) -> dict:
    core = field.get("source") == "core"
    core_key = field.get("core_key") if core else None
    reg = registry.get(core_key) if core else None
    ftype = type_of(field, registry)
    module_type = ftype in MODULE_TYPES
    stored_rules = dict(field.get("validation") or {})
    allowed_rules = set(RULES.get(ftype, {})) if ftype in RULES else set()
    validation = {k: v for k, v in stored_rules.items() if k in allowed_rules}
    extra_rules = {k: v for k, v in stored_rules.items() if k not in allowed_rules}    # older rules this contract has no name for: kept, and written back unchanged
    if core_key in SINGLE_VALUE_KEYS:
        validation.setdefault("max_items", SINGLE_VALUE_KEYS[core_key])
    key = core_key if core else (field.get("stable_key") or str(field.get("id")))
    out = {
        "id": str(field.get("id") or key), "key": key, "kind": "core" if core else "custom", "type": ftype,
        "renderer": MODULE_TYPES[ftype] if module_type else renderer_of(ftype), "label": field.get("label") or (reg or {}).get("display_name") or key,
        "placeholder": field.get("placeholder"), "help_text": field.get("help_text"), "required": bool(field.get("required", False)),
        "enabled": bool(field.get("is_enabled", True)), "order": int(field.get("position") or 0), "validation": validation,
        "options": [{"value": str(o.get("value")), "label": o.get("label") or str(o.get("value"))} for o in field.get("options") or [] if isinstance(o, dict) and o.get("value") is not None],
        "visibility": _visibility(field), "system": bool(core and core_key in domain_required),
        "legacy_renderer": field.get("renderer"), "source": field.get("source") or "custom",
    }
    if module_type:
        out["module_type"] = True
    if extra_rules:
        out["legacy_validation"] = extra_rules
    if core_key:
        out["core_key"] = core_key
        if reg and reg.get("value_source") not in (None, "static"):
            out["options_source"] = reg["value_source"]
    if field.get("composite_config"):
        out["composite_config"] = field["composite_config"]
    return out


def to_common(sections: list[dict] | None, *, registry: dict, domain_required, include_disabled: bool = True) -> dict:
    """The stored sections as a common definition. `include_disabled` keeps switched-off sections and fields (the builder needs them); the projections drop them for everyone else."""
    out = []
    for index, section in enumerate(sorted(sections or [], key=lambda s: int(s.get("position") or 0)), start=1):
        if not include_disabled and not section.get("is_enabled", True):
            continue
        fields = [field_to_common(f, registry, domain_required) for f in sorted(section.get("fields") or [], key=lambda f: int(f.get("position") or 0))
                  if include_disabled or f.get("is_enabled", True)]
        out.append({"id": str(section.get("id") or section.get("stable_key") or index), "key": section.get("stable_key") or f"section_{index}", "title": section.get("label") or "Section",
                    "description": section.get("description"), "order": int(section.get("position") or index), "enabled": bool(section.get("is_enabled", True)),
                    "visibility": "enterprise_admin_only" if str(section.get("visibility") or "").lower() in ("enterprise_admin_only", "internal") else "published", "fields": fields})
    return {"sections": out}


def values_by_key(definition: dict, custom_values) -> dict:
    """The custom values an Event or Training stores (`[{field_id, value}]`, or `{key: value}`) as a dict by field key."""
    fields = [f for s in definition.get("sections", []) for f in s.get("fields", [])]
    by_id = {f["id"]: f["key"] for f in fields}
    keys = {f["key"] for f in fields}
    if isinstance(custom_values, dict):
        return {k: v for k, v in custom_values.items() if k in keys}
    out = {}
    for item in custom_values or []:
        if not isinstance(item, dict):
            continue
        key = by_id.get(str(item.get("field_id") or item.get("id") or "")) or (item.get("stable_key") if item.get("stable_key") in keys else None)
        if key is not None:
            out[key] = item.get("value")
    return out
