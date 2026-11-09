"""One form, three answers: what each kind of caller may be shown.

    public            what the public and mobile apps get: only enabled fields marked `published`, the least the app needs to show them
    enterprise_admin  the Enterprise Admin's own form: every enabled field, with placeholders, help, required and rules to build the input screen
    super_admin       the form builder: everything, disabled fields and protected flags included

`project_form` shapes a definition; `project_values` shapes the values saved with a post, event or training. An `enterprise_admin_only`
field, and the files and links saved in it, never reach the `public` answer, and neither does anything about a disabled field.
Field types are returned as they are (`short_text`, `video`, `additional_images`, ...), never turned into text, textarea or select.
"""
from app.form_builder.fields import MEDIA

AUDIENCES = ("public", "enterprise_admin", "super_admin")
PUBLIC_FIELD = ("id", "key", "label", "type", "renderer", "order", "options")
ADMIN_FIELD = PUBLIC_FIELD + ("placeholder", "help_text", "required", "enabled", "validation", "visibility")
PUBLIC_EXTRA = ("module_type",)                                                  # present only on fields that have it
ADMIN_EXTRA = ("module_type", "core_key", "composite_config", "options_source")   # for the Event and Training forms' built-in fields
PUBLIC_ITEM = ("url", "name", "size", "content_type", "alt", "caption", "label", "provider", "video_id", "thumbnail_url")


def _check(audience: str) -> None:
    if audience not in AUDIENCES:
        raise ValueError(f"audience must be one of: {', '.join(AUDIENCES)}")


def section_visible_to(section: dict, audience: str) -> bool:
    """A switched-off section is for the builder only; an `enterprise_admin_only` section (and everything in it) is not for the public."""
    if audience == "super_admin":
        return True
    if section.get("enabled", True) is False:
        return False
    return audience == "enterprise_admin" or section.get("visibility", "published") == "published"


def visible_to(field: dict, audience: str) -> bool:
    if audience == "super_admin":
        return True
    if not field.get("enabled", True):
        return False
    return audience == "enterprise_admin" or field.get("visibility") == "published"


def project_field(field: dict, audience: str) -> dict:
    _check(audience)
    if audience == "super_admin":
        return dict(field)
    keys = PUBLIC_FIELD if audience == "public" else ADMIN_FIELD
    out = {k: field.get(k) for k in keys if k != "options_source"}
    for extra in (PUBLIC_EXTRA if audience == "public" else ADMIN_EXTRA):
        if extra in field:
            out[extra] = field[extra]
    if audience == "enterprise_admin":
        out["protected"] = bool(field.get("system"))
    return out


def project_form(definition: dict, audience: str) -> dict:
    _check(audience)
    sections = []
    for section in (definition or {}).get("sections", []):
        if not section_visible_to(section, audience):
            continue
        fields = [project_field(f, audience) for f in section.get("fields", []) if visible_to(f, audience)]
        if fields or audience == "super_admin":
            out = {"id": section.get("id"), "key": section.get("key"), "title": section.get("title"), "description": section.get("description"), "order": section.get("order"), "fields": fields}
            if audience != "public":
                out["visibility"] = section.get("visibility", "published")
                out["enabled"] = section.get("enabled", True)
            sections.append(out)
    return {"sections": sections}


def _public_value(field: dict, value):
    if field["type"] in MEDIA or field["type"] == "reference_url":
        if isinstance(value, list):
            return [{k: v for k, v in item.items() if k in PUBLIC_ITEM and v is not None} for item in value]
        if isinstance(value, dict):
            return {k: v for k, v in value.items() if k in PUBLIC_ITEM and v is not None}
    return value


def project_values(definition: dict, values: dict | None, audience: str) -> list[dict]:
    """The saved values, in form order, with each field's label and type. Public: published fields only, with no internal ids."""
    _check(audience)
    out = []
    for section in (definition or {}).get("sections", []):
        if not section_visible_to(section, audience):
            continue
        for field in section.get("fields", []):
            value = (values or {}).get(field["key"])
            if value in (None, "", [], {}) or not visible_to(field, audience):
                continue
            item = {"key": field["key"], "label": field["label"], "type": field["type"], "renderer": field["renderer"],
                    "value": _public_value(field, value) if audience == "public" else value}
            if audience != "public":
                item["visibility"] = field.get("visibility")
            out.append(item)
    return out
