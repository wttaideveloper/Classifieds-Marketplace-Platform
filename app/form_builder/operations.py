"""Builder operations on a form definition: sections and fields, as pure functions.

A definition is `{"sections": [{"id", "key", "title", "description", "order", "fields": [field, ...]}]}` (see fields.py for a field). Every
function takes a definition and returns `(new_definition, summary)` without changing its input; `summary` is a sentence for the audit
log. Positions are 1-based and `order` is the position inside the section. Problems raise `BuilderError`, which the module's adapter turns
into an HTTP answer.

Protected (`system`) fields belong to the module, so they cannot be deleted, re-keyed, re-typed, switched off, made optional or taken
out of the published content; their label, help text, placeholder and position may change.
"""
import copy
import re
import uuid

from app.form_builder.fields import err, normalize_field

MAX_SECTIONS, MAX_FIELDS = 20, 60


class BuilderError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 422, errors: list | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.errors = code, message, status, errors or []


def _clean(text, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:30] or "section"


def renumber(definition: dict) -> dict:
    for i, section in enumerate(definition["sections"], start=1):
        section["order"] = i
        for j, field in enumerate(section["fields"], start=1):
            field["order"] = j
    return definition


def all_fields(definition: dict):
    return [f for s in definition.get("sections", []) for f in s.get("fields", [])]


def _section(definition: dict, section_id: str) -> dict:
    for section in definition["sections"]:
        if section["id"] == section_id or section["key"] == section_id:
            return section
    raise BuilderError("SECTION_NOT_FOUND", "That section does not exist", status=404)


def _locate(definition: dict, field_ref: str) -> tuple[dict, dict]:
    for section in definition["sections"]:
        for field in section["fields"]:
            if field["id"] == field_ref or field["key"] == field_ref:
                return section, field
    raise BuilderError("FIELD_NOT_FOUND", "That field does not exist", status=404)


def _insert(items: list, item, position: int | None) -> None:
    index = len(items) if position is None else max(0, min(len(items), position - 1))
    items.insert(index, item)


def _check_unique_label(section: dict, label: str, ignore_id: str | None = None) -> None:
    if any(f["label"].lower() == label.lower() and f["id"] != ignore_id for f in section["fields"]):
        raise BuilderError("DUPLICATE_LABEL", f"This section already has a field labelled '{label}'", status=409, errors=[err("label", "DUPLICATE_LABEL", f"'{label}' is used twice")])


def _check_unique_key(definition: dict, key: str) -> None:
    if any(f["key"] == key for f in all_fields(definition)):
        raise BuilderError("DUPLICATE_KEY", f"The key '{key}' is already used by another field", status=409, errors=[err("key", "DUPLICATE_KEY", f"'{key}' is used twice")])


SECTION_VISIBILITIES = ("published", "enterprise_admin_only")


def _check_visibility(value) -> str:
    text = str(value or "published").strip().lower()
    if text not in SECTION_VISIBILITIES:
        raise BuilderError("VALIDATION_FAILED", f"visibility must be one of: {', '.join(SECTION_VISIBILITIES)}", errors=[err("visibility", "INVALID", "visibility must be published or enterprise_admin_only")])
    return text


def new_section_dict(title: str, description=None, key: str | None = None, visibility: str = "published") -> dict:
    return {"id": str(uuid.uuid4()), "key": key or _slug(title), "title": _clean(title, 120), "description": _clean(description, 300) or None, "order": 0,
            "visibility": visibility, "enabled": True, "fields": []}


# ---------------------------------------------------------------- sections

def add_section(definition: dict, *, title: str, description=None, position: int | None = None, visibility: str = "published") -> tuple[dict, str]:
    d = copy.deepcopy(definition)
    title = _clean(title, 120)
    if not title:
        raise BuilderError("VALIDATION_FAILED", "A section needs a title", errors=[err("title", "REQUIRED", "A section needs a title")])
    if len(d["sections"]) >= MAX_SECTIONS:
        raise BuilderError("TOO_MANY_SECTIONS", f"A form can have at most {MAX_SECTIONS} sections")
    if any(s["title"].lower() == title.lower() for s in d["sections"]):
        raise BuilderError("DUPLICATE_SECTION", f"There is already a section titled '{title}'", status=409)
    section = new_section_dict(title, description, visibility=_check_visibility(visibility))
    keys = {s["key"] for s in d["sections"]}
    while section["key"] in keys:
        section["key"] = f"{section['key']}_{len(keys) + 1}"
    _insert(d["sections"], section, position)
    return renumber(d), f"Added section '{title}'"


def edit_section(definition: dict, section_id: str, *, title=None, description=None, visibility=None, enabled=None) -> tuple[dict, str]:
    d = copy.deepcopy(definition)
    section = _section(d, section_id)
    if title is not None:
        title = _clean(title, 120)
        if not title:
            raise BuilderError("VALIDATION_FAILED", "A section needs a title", errors=[err("title", "REQUIRED", "A section needs a title")])
        if any(s["title"].lower() == title.lower() and s is not section for s in d["sections"]):
            raise BuilderError("DUPLICATE_SECTION", f"There is already a section titled '{title}'", status=409)
        section["title"] = title
    if description is not None:
        section["description"] = _clean(description, 300) or None
    if visibility is not None:
        section["visibility"] = _check_visibility(visibility)
    if enabled is not None:
        section["enabled"] = bool(enabled)
    return d, f"Edited section '{section['title']}'"


def delete_section(definition: dict, section_id: str, *, move_to: str | None = None) -> tuple[dict, str]:
    """An empty section is deleted. One with fields needs `move_to` (they move there), and one with a protected field can only be emptied that way."""
    d = copy.deepcopy(definition)
    section = _section(d, section_id)
    if len(d["sections"]) == 1:
        raise BuilderError("LAST_SECTION", "A form needs at least one section")
    if section["fields"]:
        if not move_to:
            code = "SECTION_HAS_PROTECTED_FIELDS" if any(f.get("system") for f in section["fields"]) else "SECTION_NOT_EMPTY"
            raise BuilderError(code, "This section has fields. Move them to another section (move_to) or delete them first", status=409)
        target = _section(d, move_to)
        if target is section:
            raise BuilderError("VALIDATION_FAILED", "move_to must be a different section")
        target["fields"].extend(section["fields"])
    d["sections"].remove(section)
    return renumber(d), f"Deleted section '{section['title']}'"


def reorder_sections(definition: dict, ids: list[str]) -> tuple[dict, str]:
    d = copy.deepcopy(definition)
    by_ref = {s["id"]: s for s in d["sections"]}
    if len(set(ids)) != len(ids) or set(ids) != set(by_ref):
        raise BuilderError("INVALID_ORDER", "Send every section id exactly once, in the new order", errors=[err("ids", "INVALID_ORDER", "Every section once")])
    d["sections"] = [by_ref[i] for i in ids]
    return renumber(d), "Reordered the sections"


# ---------------------------------------------------------------- fields

def add_field(definition: dict, section_id: str, raw: dict, *, position: int | None = None, system: bool = False) -> tuple[dict, dict, str]:
    """Returns (definition, the new field, summary)."""
    d = copy.deepcopy(definition)
    section = _section(d, section_id)
    if len(all_fields(d)) >= MAX_FIELDS:
        raise BuilderError("TOO_MANY_FIELDS", f"A form can have at most {MAX_FIELDS} fields")
    errors: list = []
    field = normalize_field(raw, path="field", errors=errors, system=system)
    if errors or field is None:
        raise BuilderError("VALIDATION_FAILED", errors[0]["message"] if len(errors) == 1 else "The field has problems", errors=errors)
    _check_unique_key(d, field["key"])
    _check_unique_label(section, field["label"])
    _insert(section["fields"], field, position)
    renumber(d)
    return d, field, f"Added field '{field['key']}' ({field['type']}) to section '{section['title']}'"


def _check_protected_edit(old: dict, new: dict) -> None:
    if not old.get("system"):
        return
    problems = []
    if not new["enabled"]:
        problems.append(err("enabled", "PROTECTED_FIELD", f"'{old['key']}' is a system field and cannot be switched off"))
    if old["required"] and not new["required"]:
        problems.append(err("required", "PROTECTED_FIELD", f"'{old['key']}' is a system field and must stay required"))
    if old["visibility"] == "published" and new["visibility"] != "published":
        problems.append(err("visibility", "PROTECTED_FIELD", f"'{old['key']}' is a system field and must stay published"))
    if problems:
        raise BuilderError("PROTECTED_FIELD", problems[0]["message"], status=409, errors=problems)


def edit_field(definition: dict, field_ref: str, patch: dict) -> tuple[dict, dict, str]:
    """Changes what was sent. A key or type in the patch must equal the stored one (they never change)."""
    d = copy.deepcopy(definition)
    section, old = _locate(d, field_ref)
    merged = {**old, **{k: v for k, v in patch.items() if k not in ("id", "order", "system", "renderer")}}
    errors: list = []
    new = normalize_field(merged, path="field", errors=errors, existing=old)
    if errors or new is None:
        raise BuilderError("VALIDATION_FAILED", errors[0]["message"] if len(errors) == 1 else "The field has problems", errors=errors)
    _check_protected_edit(old, new)
    _check_unique_label(section, new["label"], ignore_id=old["id"])
    new["order"] = old["order"]
    section["fields"][section["fields"].index(old)] = new
    return d, new, f"Edited field '{new['key']}'"


def delete_field(definition: dict, field_ref: str) -> tuple[dict, str]:
    d = copy.deepcopy(definition)
    section, field = _locate(d, field_ref)
    if field.get("system"):
        raise BuilderError("PROTECTED_FIELD", f"'{field['key']}' is a system field and cannot be deleted", status=409,
                           errors=[err("field", "PROTECTED_FIELD", "A system field cannot be deleted")])
    section["fields"].remove(field)
    return renumber(d), f"Deleted field '{field['key']}'"


def duplicate_field(definition: dict, field_ref: str) -> tuple[dict, dict, str]:
    """A copy right after the original, with a new id and a new key (`<key>_copy`, `<key>_copy2` ...) and the label '<label> (copy)'. Never a system field."""
    d = copy.deepcopy(definition)
    section, field = _locate(d, field_ref)
    if len(all_fields(d)) >= MAX_FIELDS:
        raise BuilderError("TOO_MANY_FIELDS", f"A form can have at most {MAX_FIELDS} fields")
    keys = {f["key"] for f in all_fields(d)}
    labels = {f["label"].lower() for f in section["fields"]}
    n, key = 1, f"{field['key'][:34]}_copy"
    while key in keys:
        n += 1
        key = f"{field['key'][:33]}_copy{n}"
    label = f"{field['label'][:110]} (copy)"
    while label.lower() in labels:
        label = f"{label[:-1]} {n})"
    clone = copy.deepcopy(field)
    clone.update({"id": str(uuid.uuid4()), "key": key, "label": label, "system": False})
    section["fields"].insert(section["fields"].index(field) + 1, clone)
    return renumber(d), clone, f"Duplicated field '{field['key']}' as '{key}'"


def reorder_fields(definition: dict, section_id: str, ids: list[str]) -> tuple[dict, str]:
    d = copy.deepcopy(definition)
    section = _section(d, section_id)
    by_id = {f["id"]: f for f in section["fields"]}
    if len(set(ids)) != len(ids) or set(ids) != set(by_id):
        raise BuilderError("INVALID_ORDER", "Send every field id of the section exactly once, in the new order", errors=[err("ids", "INVALID_ORDER", "Every field once")])
    section["fields"] = [by_id[i] for i in ids]
    return renumber(d), f"Reordered the fields of section '{section['title']}'"


def move_field(definition: dict, field_ref: str, to_section_id: str, *, position: int | None = None) -> tuple[dict, dict, str]:
    d = copy.deepcopy(definition)
    source, field = _locate(d, field_ref)
    target = _section(d, to_section_id)
    if target is not source:
        _check_unique_label(target, field["label"])
    source["fields"].remove(field)
    _insert(target["fields"], field, position)
    renumber(d)
    return d, field, f"Moved field '{field['key']}' to section '{target['title']}'"
