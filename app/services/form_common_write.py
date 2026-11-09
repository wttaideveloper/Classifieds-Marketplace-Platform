"""Building Event and Training forms in the common contract: field and section operations on the draft, review and submit, and checking values of the new field types.

The builders keep their own stored shape (sections of fields with `source`, `core_key`, `stable_key`, `renderer`, `position`, `is_enabled` ...). An operation here converts the draft to the
common definition, runs one pure operation from app/form_builder/operations.py, and writes the result back **onto the stored fields**: a field an operation did not touch keeps every key it had.
- A built-in (core) field cannot be deleted or duplicated; its label, help text, placeholder, `required`, `enabled` and position can change (a protected domain-required one cannot be switched off
  or made optional). Its type, key, renderer and composite settings are the module's.
- A field added here is a custom field with an explicit `field_type`; it is checked with the shared rules. Fields made before keep their older checks.
- The draft keeps switched-off fields (the older save route drops them, so use this builder to keep them).
"""
import copy
import hashlib
import json
import logging
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.exceptions.problem import problem
from app.form_builder import legacy_sections
from app.form_builder import operations as ops
from app.form_builder import projection
from app.form_builder.fields import TYPES, err, validate_value
from app.services import form_common_view as views

# the older renderer name a field of this type is stored with; a type without an older name is stored with its own renderer
logger = logging.getLogger("form_builder")
LEGACY_RENDERER = {"short_text": "text", "long_text": "textarea", "dropdown": "select", "checkbox": "multi_select", "number": "number", "date": "date", "reference_url": "url",
                   "datetime": "datetime", "toggle": "checkbox"}
VALUE_TYPE = {"number": "number", "toggle": "boolean", "checkbox": "string[]", "tags": "array", "additional_images": "array", "supporting_document": "array", "video": "array"}
NEW_RENDERERS = {t: TYPES[t]["renderer"] for t in TYPES}      # renderer names of the types that have no older name


def content_hash(sections) -> str:
    return hashlib.sha256(json.dumps(sections or [], sort_keys=True, default=str).encode()).hexdigest()


def renderer_names() -> set[str]:
    """The renderer names a custom field may have, on top of the older ones: the common renderers of the new types."""
    return {TYPES[t]["renderer"] for t in TYPES if t not in LEGACY_RENDERER}


# ---------------------------------------------------------------- checking a value of a field made in this builder

def check_value(field_def: dict, value, sp: views.Spec):
    """The stored form of a value for a field that has an explicit `field_type`. Raises 400 with the first problem (these services answer 400 with a sentence)."""
    common = legacy_sections.field_to_common(field_def, sp.registry, sp.domain_required)
    errors: list[dict] = []
    from app.utils.html_sanitize import clean_html

    cleaned = validate_value(common, value, "value", errors, sanitize_html=clean_html)
    if errors:
        raise HTTPException(status_code=400, detail=errors[0]["message"])
    return cleaned


def split_private(items: list[dict], sections: list[dict], existing_private: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """`(public, private)`: the values of fields marked `enterprise_admin_only` leave the public list. For a private field not mentioned in `items` the stored value is kept;
    a private field sent with a null value is cleared."""
    private = lambda value: str(value or "").lower() in ("enterprise_admin_only", "internal")      # noqa: E731
    private_ids = {f["id"] for s in sections for f in s.get("fields") or [] if private(f.get("visibility")) or private(s.get("visibility"))}      # a field in a private section is private
    public = [i for i in items if str(i.get("field_id")) not in private_ids]
    sent = {str(i["field_id"]): i for i in items if str(i.get("field_id")) in private_ids}
    kept = [i for i in (existing_private or []) if str(i.get("field_id")) in private_ids and str(i.get("field_id")) not in sent]
    return public, kept + [i for i in sent.values() if i.get("value") is not None]


# ---------------------------------------------------------------- common definition <-> stored sections

def _legacy_field(common: dict, original: dict | None) -> dict:
    """The stored field for a common field. For a field that existed, only what differs from what was stored is written, so an untouched field is stored exactly as it was."""
    out = copy.deepcopy(original) if original else {"id": common["id"], "source": "custom", "stable_key": common["key"], "core_key": None}

    def put(key, value):
        if (out.get(key) if key in out else None) != value and not (value in (None, [], {}) and key not in out):
            out[key] = value

    put("label", common["label"])
    put("placeholder", common.get("placeholder"))
    put("help_text", common.get("help_text"))
    put("required", bool(common["required"]))
    put("is_enabled", bool(common["enabled"]))
    out["position"] = common["order"]
    merged_rules = {**(common.get("legacy_validation") or {}), **(common.get("validation") or {})}
    if common.get("kind") == "custom":
        put("options", list(common.get("options") or []))
    put("validation", merged_rules)
    if original is None:                                              # a field made here: explicit type, an older renderer name where there is one
        out["field_type"] = common["type"]
        out["renderer"] = LEGACY_RENDERER.get(common["type"]) or NEW_RENDERERS[common["type"]]
        out["value_type"] = VALUE_TYPE.get(common["type"], "string")
    if common.get("visibility") == "enterprise_admin_only" or "visibility" in (original or {}):
        put("visibility", common.get("visibility", "published"))
    return out


def from_common(definition: dict, originals: list[dict] | None) -> list[dict]:
    fields_by_id = {f["id"]: f for s in originals or [] for f in s.get("fields") or []}
    sections_by_id = {s.get("id"): s for s in originals or []}
    out = []
    for position, section in enumerate(definition["sections"], start=1):
        legacy = copy.deepcopy(sections_by_id.get(section["id"], {}))
        legacy.update({"id": section["id"], "stable_key": section["key"], "label": section["title"], "description": section.get("description"), "position": position,
                       "is_enabled": section.get("enabled", True)})
        if section.get("visibility") == "enterprise_admin_only" or "visibility" in legacy:
            legacy["visibility"] = section.get("visibility", "published")
        legacy["fields"] = [_legacy_field(f, fields_by_id.get(f["id"])) for f in section["fields"]]
        out.append(legacy)
    return out


# ---------------------------------------------------------------- the draft

def _builder_error(exc: ops.BuilderError):
    return problem(exc.status, exc.message, exc.code, **({"errors": exc.errors} if exc.errors else {}))


def ensure_draft(db: Session, sp: views.Spec, config, user: dict):
    """The configuration's draft version. Editing a published configuration forks a new draft from it, as the older save route does."""
    draft = sp.service._get_draft_version(db, config.id)
    if draft is not None:
        return draft
    actor = sp.service._actor(user)
    if config.status == "published":
        published = sp.service._get_published_version(db, config.id)
        if published is None:
            raise problem(409, "There is no published version to start a draft from", "NO_PUBLISHED_VERSION")
        number = config.current_version + 1
        draft = sp.version_model(configuration_id=config.id, version=number, status="draft", sections=copy.deepcopy(published.sections or []), created_by=actor)
        config.current_version, config.status = number, "draft"
    else:
        draft = sp.version_model(configuration_id=config.id, version=config.current_version or 1, status="draft", sections=[], created_by=actor)
    db.add(draft)
    db.flush()
    return draft


def _etag_check(draft, base_etag: str | None) -> None:
    from app.form_builder.concurrency import current_if_match

    base_etag = base_etag if base_etag is not None else current_if_match()          # an If-Match header, when the request has no base_etag
    current = content_hash(draft.sections)
    if base_etag is not None and base_etag != current:
        raise problem(409, "The form was changed by someone else after you opened it. Reload it, then apply your changes again", "VERSION_CONFLICT", current_etag=current)


def build(db: Session, module: str, config_id: UUID, user: dict, base_etag: str | None, operation) -> dict:
    """One builder operation on the draft. `operation(definition)` returns (new definition, summary, action, detail)."""
    sp = views.spec(module)
    config = sp.service._get_config_or_404(db, config_id)
    draft = ensure_draft(db, sp, config, user)
    _etag_check(draft, base_etag)
    before = content_hash(draft.sections)
    definition = legacy_sections.to_common(draft.sections, registry=sp.registry, domain_required=sp.domain_required)
    try:
        new, summary, action, detail = operation(definition)
    except ops.BuilderError as exc:
        raise _builder_error(exc)
    draft.sections = from_common(new, draft.sections)
    draft.reviewed_hash = None
    config.updated_at = datetime.utcnow()
    sp.service._audit(db, configuration_id=config.id, version_id=draft.id, action=f"common_{action}", actor_id=sp.service._actor(user),
                      before={"etag": before}, after={"summary": summary, **(detail or {}), "etag": content_hash(draft.sections)})
    db.commit()
    logger.info("form_builder module=%s form_id=%s action=%s actor=%s", module, config.id, f"common_{action}", sp.service._actor(user))
    out = views.builder_view(db, module, config_id)
    out["change"] = {"action": action, "summary": summary, **(detail or {})}
    return out


# ---- operations (field refs are an id or a key; section refs an id or a key)

def _is_core(definition: dict, ref: str) -> dict | None:
    section, field = ops._locate(definition, ref)
    return field if field.get("kind") == "core" else None


def add_section(db, module, config_id, user, base_etag, *, title, description, position, visibility="published"):
    return build(db, module, config_id, user, base_etag, lambda d: (*ops.add_section(d, title=title, description=description, position=position, visibility=visibility), "section_added", None))


def edit_section(db, module, config_id, user, base_etag, section_id, *, title, description, visibility=None, enabled=None):
    return build(db, module, config_id, user, base_etag, lambda d: (*ops.edit_section(d, section_id, title=title, description=description, visibility=visibility, enabled=enabled),
                                                                    "section_edited", {"section_id": section_id}))


def delete_section(db, module, config_id, user, base_etag, section_id, *, move_to):
    return build(db, module, config_id, user, base_etag, lambda d: (*ops.delete_section(d, section_id, move_to=move_to), "section_deleted", {"section_id": section_id, "moved_to": move_to}))


def reorder_sections(db, module, config_id, user, base_etag, ids):
    return build(db, module, config_id, user, base_etag, lambda d: (*ops.reorder_sections(d, [str(i) for i in ids]), "sections_reordered", None))


def add_field(db, module, config_id, user, base_etag, *, section_id, field, position):
    def operation(d):
        key = str((field or {}).get("key") or "")
        sp = views.spec(module)
        if key in sp.registry:
            raise ops.BuilderError("KEY_RESERVED", f"'{key}' is one of the built-in {module} fields. Edit that field instead of adding it", status=409,
                                   errors=[err("key", "KEY_RESERVED", f"'{key}' is a built-in field")])
        new, added, summary = ops.add_field(d, section_id, field, position=position)
        return new, summary, "field_added", {"field_id": added["id"], "key": added["key"], "type": added["type"]}

    return build(db, module, config_id, user, base_etag, operation)


def edit_field(db, module, config_id, user, base_etag, field_ref, patch):
    def operation(d):
        core = _is_core(d, field_ref)
        if core is None:
            new, edited, summary = ops.edit_field(d, field_ref, patch)
            return new, summary, "field_edited", {"field_id": edited["id"], "key": edited["key"], "changed": sorted(patch)}
        allowed = {"label", "placeholder", "help_text", "required", "enabled"}
        forbidden = sorted(k for k in patch if k not in allowed and not (k in ("key", "type", "id") and patch[k] == core.get(k)))
        if forbidden:
            raise ops.BuilderError("CORE_FIELD", f"'{core['key']}' is a built-in field: only {', '.join(sorted(allowed))} can be changed here", status=409,
                                   errors=[err(k, "CORE_FIELD", f"{k} of a built-in field cannot be changed here") for k in forbidden])
        new_field = {**core, **{k: v for k, v in patch.items() if k in allowed}}
        new_field["required"] = bool(new_field["required"]) and bool(new_field["enabled"])
        ops._check_protected_edit(core, new_field)
        d = copy.deepcopy(d)
        section, old = ops._locate(d, field_ref)
        section["fields"][section["fields"].index(old)] = new_field
        return d, f"Edited field '{core['key']}'", "field_edited", {"field_id": core["id"], "key": core["key"], "changed": sorted(patch)}

    return build(db, module, config_id, user, base_etag, operation)


def delete_field(db, module, config_id, user, base_etag, field_ref):
    def operation(d):
        core = _is_core(d, field_ref)
        if core is not None:
            code = "PROTECTED_FIELD" if core.get("system") else "CORE_FIELD"
            raise ops.BuilderError(code, f"'{core['key']}' is a built-in field and cannot be deleted" + ("" if core.get("system") else ". Switch it off (enabled: false) instead"), status=409,
                                   errors=[err("field", code, "A built-in field cannot be deleted")])
        new, summary = ops.delete_field(d, field_ref)
        _, field = ops._locate(d, field_ref)
        return new, summary, "field_deleted", {"field_id": field["id"], "key": field["key"]}

    return build(db, module, config_id, user, base_etag, operation)


def duplicate_field(db, module, config_id, user, base_etag, field_ref):
    def operation(d):
        core = _is_core(d, field_ref)
        if core is not None:
            raise ops.BuilderError("CORE_FIELD", f"'{core['key']}' is a built-in field and cannot be duplicated", status=409, errors=[err("field", "CORE_FIELD", "A built-in field cannot be duplicated")])
        new, clone, summary = ops.duplicate_field(d, field_ref)
        return new, summary, "field_duplicated", {"field_id": clone["id"], "key": clone["key"], "copied_from": field_ref}

    return build(db, module, config_id, user, base_etag, operation)


def reorder_fields(db, module, config_id, user, base_etag, section_id, ids):
    return build(db, module, config_id, user, base_etag, lambda d: (*ops.reorder_fields(d, section_id, [str(i) for i in ids]), "fields_reordered", {"section_id": section_id}))


def move_field(db, module, config_id, user, base_etag, field_ref, *, section_id, position):
    def operation(d):
        new, moved, summary = ops.move_field(d, field_ref, section_id, position=position)
        return new, summary, "field_moved", {"field_id": moved["id"], "key": moved["key"], "section_id": section_id}

    return build(db, module, config_id, user, base_etag, operation)


# ---------------------------------------------------------------- review and submit

def _changes(sp: views.Spec, db: Session, config, draft) -> dict:
    published = sp.service._get_published_version(db, config.id)
    after = {f["key"]: f for s in legacy_sections.to_common(draft.sections, registry=sp.registry, domain_required=sp.domain_required)["sections"] for f in s["fields"]}
    before = ({f["key"]: f for s in legacy_sections.to_common(published.sections, registry=sp.registry, domain_required=sp.domain_required)["sections"] for f in s["fields"]}
              if published is not None else {})
    comparable = lambda f: {k: v for k, v in f.items() if k not in ("order", "id")}    # noqa: E731
    changed = []
    for key in before.keys() & after.keys():
        diff = sorted(k for k in comparable(before[key]).keys() | comparable(after[key]).keys() if comparable(before[key]).get(k) != comparable(after[key]).get(k))
        if diff:
            changed.append({"key": key, "label": after[key]["label"], "changed": diff})
    return {"since_version": published.version if published else None, "added": sorted(after.keys() - before.keys()), "removed": sorted(before.keys() - after.keys()),
            "changed": sorted(changed, key=lambda c: c["key"])}


def review(db: Session, module: str, config_id: UUID) -> dict:
    """Is the draft ready to publish? Every problem the publish check finds, plus what the draft changes against the published version. Nothing is saved."""
    sp = views.spec(module)
    config = sp.service._get_config_or_404(db, config_id)
    draft = sp.service._get_draft_version(db, config.id)
    if draft is None:
        raise problem(409, "There is no draft to review", "NO_DRAFT")
    problems: list[dict] = []
    try:
        sp.service.validate_sections_for_publish(sp.service.normalize_sections(draft.sections or [], assign_ids=False), scope=config.scope)
    except HTTPException as exc:
        detail = exc.detail
        messages = detail.get("errors") if isinstance(detail, dict) else [str(detail)]
        problems = [err("definition", "INVALID_FORM", m if isinstance(m, str) else str(m)) for m in messages or []]
    definition = legacy_sections.to_common(draft.sections, registry=sp.registry, domain_required=sp.domain_required)
    current = content_hash(draft.sections)
    return {"configuration_id": config.id, "version_id": draft.id, "version": draft.version, "etag": current, "ready": not problems, "problems": problems, "warnings": [],
            "changes": _changes(sp, db, config, draft), "section_count": len(definition["sections"]), "field_count": sum(len(s["fields"]) for s in definition["sections"]),
            "review_status": "submitted" if draft.reviewed_hash == current else "draft", "review_required_to_publish": bool(settings.FORM_REQUIRE_REVIEW)}


def submit(db: Session, module: str, config_id: UUID, user: dict, base_etag: str | None) -> dict:
    sp = views.spec(module)
    config = sp.service._get_config_or_404(db, config_id)
    draft = sp.service._get_draft_version(db, config.id)
    if draft is None:
        raise problem(409, "There is no draft to submit", "NO_DRAFT")
    _etag_check(draft, base_etag)
    result = review(db, module, config_id)
    if not result["ready"]:
        raise problem(422, "The draft has problems. Fix them before you submit it", "FORM_NOT_READY", errors=result["problems"])
    draft.reviewed_hash, draft.reviewed_at, draft.reviewed_by = content_hash(draft.sections), datetime.utcnow(), sp.service._actor(user)
    sp.service._audit(db, configuration_id=config.id, version_id=draft.id, action="common_submitted", actor_id=sp.service._actor(user), before=None,
                      after={"etag": draft.reviewed_hash, "changes": result["changes"]})
    db.commit()
    return review(db, module, config_id)


def diagnostics(db: Session, module: str) -> dict:
    """Numbers for monitoring and for checking a migration or a rollback: configurations by status, active ones, drafts waiting for review, records using private values."""
    from collections import Counter

    sp = views.spec(module)
    configs = db.query(sp.config_model).all()
    versions = db.query(sp.version_model).all()
    record_model = sp.service.__dict__.get("Event") or sp.service.__dict__.get("Training")
    return {"generated_at": datetime.utcnow(), "configurations": {"total": len(configs), "by_status": dict(Counter(c.status for c in configs)), "active": sum(1 for c in configs if c.is_active),
                                                                   "by_scope": dict(Counter(c.scope for c in configs))},
            "versions": {"total": len(versions), "drafts": sum(1 for v in versions if v.status == "draft"),
                         "drafts_submitted_for_review": sum(1 for v in versions if v.status == "draft" and v.reviewed_hash and v.reviewed_hash == content_hash(v.sections)),
                         "with_enterprise_only_fields": sum(1 for v in versions if any(str(f.get("visibility") or "").lower() == "enterprise_admin_only" or str(s.get("visibility") or "").lower() == "enterprise_admin_only"
                                                                                         for s in v.sections or [] for f in s.get("fields") or []))},
            "records": _record_counts(db, module)}


def _record_counts(db: Session, module: str) -> dict:
    if module == "event":
        from app.models.event_model import Event as Record
    else:
        from app.models.training_model import Training as Record
    total = db.query(Record).count()
    return {"total": total, "without_form_version": db.query(Record).filter(Record.form_configuration_version_id.is_(None)).count()}


def assert_reviewed_if_required(draft) -> None:
    """Called by the publish routes: with FORM_REQUIRE_REVIEW on, a draft must have been submitted and not edited since."""
    if settings.FORM_REQUIRE_REVIEW and draft.reviewed_hash != content_hash(draft.sections):
        raise problem(409, "Submit the draft for review first (POST .../common/submit). It has changed since it was last submitted", "REVIEW_REQUIRED")
