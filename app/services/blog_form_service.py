"""Blog / news forms: Super Admin builds them, publishes versions, assigns them; a post is written against one version.

A form has a draft definition (what the Super Admin is editing) and published versions (frozen). Publishing a form copies the draft to
a new version. `is_active` says whether new posts may use it. A post keeps the version it was published with, so changing a form never
changes a live post; a post that is not live follows the active form again each time it is saved.

Which form a business gets for a kind of post: among forms that are active, published and of that kind (or "both"), one assigned to
the business ("selected") beats a global one; if several are left, the one published most recently wins. With none, the built-in
Standard form applies (version 0, no form id). Platform posts (no business) only use global forms.
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.blog_access import BlogActor
from app.core.config import settings
from app.exceptions.problem import problem
from app.form_builder import operations as ops
from app.form_builder import projection
from app.models.blog_model import BlogForm, BlogFormAssignment, BlogFormAudit, BlogFormVersion, BlogPost
from app.services import blog_form_rules as rules

FORM_TYPES = ("blog", "news", "both")
logger = logging.getLogger("form_builder")


@dataclass
class ResolvedForm:
    form_id: UUID | None
    version: int
    name: str
    mode: str
    form_type: str
    definition: dict

    def summary(self, audience: str = "enterprise_admin") -> dict:
        """The form as one kind of caller may see it: public, enterprise_admin or super_admin (see app/form_builder/projection.py)."""
        return {"form_id": self.form_id, "version": self.version, "name": self.name, "mode": self.mode, "form_type": self.form_type,
                "definition": projection.project_form(self.definition, audience)}


BUILT_IN = "Standard"


def built_in() -> ResolvedForm:
    return ResolvedForm(None, 0, BUILT_IN, "standard", "both", rules.standard_definition())


def _now() -> datetime:
    return datetime.utcnow()


def audit(db: Session, actor: BlogActor | None, form: BlogForm, action: str, summary: str, *, detail: dict | None = None, version: int | None = None) -> None:
    """One row in the form's audit history. Staged in the caller's transaction, so it is saved with the change it describes."""
    db.add(BlogFormAudit(form_id=form.id, form_name=form.name, version=version, revision=form.revision, action=action, summary=summary[:500], detail=detail,
                         actor_id=actor.id if actor else None, actor_name=actor.name if actor else None, actor_role=actor.role if actor else None))
    logger.info("form_builder module=blog form_id=%s action=%s revision=%s version=%s actor=%s", form.id, action, form.revision, version, actor.id if actor else None)


def _not_found() -> Exception:
    return problem(404, "Form not found", "FORM_NOT_FOUND")


# ---------------------------------------------------------------- finding forms

def get_form(db: Session, form_id: UUID, *, lock: bool = False) -> BlogForm:
    query = db.query(BlogForm).filter(BlogForm.id == form_id)
    row = (query.with_for_update() if lock else query).first()
    if row is None:
        raise _not_found()
    return row


def version_row(db: Session, form_id: UUID, version: int) -> BlogFormVersion | None:
    return db.query(BlogFormVersion).filter(BlogFormVersion.form_id == form_id, BlogFormVersion.version == version).first()


def _assigned(db: Session, form_id: UUID) -> list[UUID]:
    return [r.tenant_id for r in db.query(BlogFormAssignment).filter(BlogFormAssignment.form_id == form_id).all()]


def resolve_active(db: Session, kind: str, tenant_id: UUID | None) -> ResolvedForm:
    """The form a new or non-live post of `kind` by this business is written with."""
    forms = db.query(BlogForm).filter(BlogForm.is_active.is_(True), BlogForm.status == "published", BlogForm.form_type.in_([kind, "both"])).all()
    best: tuple | None = None
    for form in forms:
        selected = form.scope == "selected"
        if selected and (tenant_id is None or tenant_id not in _assigned(db, form.id)):
            continue
        row = version_row(db, form.id, form.current_version)
        if row is None:
            continue
        rank = (1 if selected else 0, row.published_at)
        if best is None or rank > best[0]:
            best = (rank, form, row)
    if best is None:
        return built_in()
    _, form, row = best
    return ResolvedForm(form.id, row.version, form.name, form.mode, form.form_type, rules.upgrade_definition(row.definition, mode=row.mode))


def resolve_pinned(db: Session, form_id: UUID | None, version: int | None) -> ResolvedForm:
    """The exact form version a post was written with. If it cannot be found, the built-in form."""
    if form_id is None or not version:
        return built_in()
    row = version_row(db, form_id, version)
    form = db.get(BlogForm, form_id)
    if row is None or form is None:
        return built_in()
    return ResolvedForm(form.id, row.version, row.name, row.mode, row.form_type, rules.upgrade_definition(row.definition, mode=row.mode))


# ---------------------------------------------------------------- responses

def _business_names(db: Session, tenant_ids: list[UUID]) -> dict[UUID, str]:
    from app.models.enterprise_model import Enterprise

    if not tenant_ids:
        return {}
    rows = db.query(Enterprise).filter(Enterprise.tenant_id.in_(tenant_ids), Enterprise.is_deleted.is_(False)).order_by(Enterprise.created_at).all()
    names: dict[UUID, str] = {}
    for row in rows:
        names.setdefault(row.tenant_id, row.business_short_name or row.business_legal_name)
    return names


def _posts_using(db: Session, form_id: UUID) -> int:
    return int(db.query(func.count(BlogPost.id)).filter(BlogPost.form_id == form_id).scalar() or 0)


def lifecycle_of(form: BlogForm, in_use: bool | None = None) -> dict:
    from app.form_builder.lifecycle import lifecycle

    return lifecycle(status=form.status, is_active=bool(form.is_active), submitted=form.review_status == "submitted" and form.reviewed_revision == form.revision,
                     has_unpublished_changes=bool(form.has_unpublished_changes), require_review=bool(settings.BLOG_FORM_REQUIRE_REVIEW), in_use=bool(in_use))


def form_dict(db: Session, form: BlogForm, *, detail: bool) -> dict:
    tenants = _assigned(db, form.id) if form.scope == "selected" else []
    out = {
        "id": form.id, "name": form.name, "description": form.description, "form_type": form.form_type, "mode": form.mode, "scope": form.scope,
        "status": form.status, "is_active": bool(form.is_active), "current_version": form.current_version, "revision": form.revision,
        "has_unpublished_changes": bool(form.has_unpublished_changes), "assigned_count": len(tenants), "post_count": _posts_using(db, form.id),
        "review_status": "submitted" if form.review_status == "submitted" and form.reviewed_revision == form.revision else "draft",
        **lifecycle_of(form),
        "created_by": form.created_by, "updated_by": form.updated_by, "created_at": form.created_at, "updated_at": form.updated_at,
    }
    if detail:
        names = _business_names(db, tenants)
        out["assignments"] = [{"tenant_id": t, "business_name": names.get(t)} for t in tenants]
        out["definition"] = rules.upgrade_definition(form.draft_definition, mode=form.mode)
    return out


# ---------------------------------------------------------------- writing

def _name(value: str) -> tuple[str, str]:
    text = " ".join((value or "").split())
    if not text:
        raise problem(422, "The form needs a name", "VALIDATION_FAILED", errors=[rules.err("name", "REQUIRED", "The form needs a name")])
    return text[:120], text[:120].lower()


def _check_name_free(db: Session, key: str, exclude: UUID | None = None) -> None:
    clash = db.query(BlogForm).filter(BlogForm.name_key == key).first()
    if clash is not None and clash.id != exclude:
        raise problem(409, "A form with this name already exists", "DUPLICATE_FORM_NAME", field="name")


def _check_revision(form: BlogForm, expected: int | None) -> None:
    if expected is None:                                    # an If-Match header, when the request has no base_revision
        from app.form_builder.concurrency import current_if_match

        header = current_if_match()
        expected = int(header) if header and header.isdigit() else None
    if expected is not None and expected != form.revision:
        raise problem(409, "Someone saved this form after you opened it. Load the latest version, then apply your changes", "VERSION_CONFLICT",
                      current_version=form.revision)


def _set_assignments(db: Session, form: BlogForm, scope: str, tenant_ids: list[UUID] | None) -> None:
    ids = list(dict.fromkeys(tenant_ids or []))
    if scope == "selected" and not ids:
        raise problem(422, "Choose at least one business for a form that is not global", "VALIDATION_FAILED",
                      errors=[rules.err("tenant_ids", "REQUIRED", "Choose at least one business")])
    if scope == "selected":
        known = _business_names(db, ids)
        unknown = [str(t) for t in ids if t not in known]
        if unknown:
            raise problem(422, "Some businesses were not found", "VALIDATION_FAILED",
                          errors=[rules.err("tenant_ids", "NOT_FOUND", f"No business for tenant {', '.join(unknown)}")])
    db.query(BlogFormAssignment).filter(BlogFormAssignment.form_id == form.id).delete(synchronize_session=False)
    if scope == "selected":
        for t in ids:
            db.add(BlogFormAssignment(form_id=form.id, tenant_id=t))
    form.scope = scope


def _touch(form: BlogForm, actor: BlogActor) -> None:
    """Any change to the draft makes it a new revision that has to be reviewed again."""
    form.revision += 1
    form.has_unpublished_changes = True
    form.review_status, form.reviewed_revision = "draft", None
    form.updated_by = actor.id


def create_form(db: Session, actor: BlogActor, payload) -> dict:
    name, key = _name(payload.name)
    _check_name_free(db, key)
    definition = rules.normalize_definition(payload.definition, mode=payload.mode)
    form = BlogForm(name=name, name_key=key, description=(payload.description or None), form_type=payload.form_type, mode=payload.mode, scope="global",
                    status="draft", is_active=False, current_version=0, draft_definition=definition, revision=1, has_unpublished_changes=True,
                    created_by=actor.id, updated_by=actor.id)
    db.add(form)
    db.flush()
    _set_assignments(db, form, payload.scope, payload.tenant_ids)
    audit(db, actor, form, "created", f"Created the form '{name}' ({payload.mode}, {payload.form_type}, {payload.scope})")
    db.commit()
    db.refresh(form)
    return form_dict(db, form, detail=True)


def update_form(db: Session, actor: BlogActor, form_id: UUID, payload) -> dict:
    """Save the draft. Posts are not affected until the form is published."""
    form = get_form(db, form_id, lock=True)
    _check_revision(form, payload.base_revision)
    data = payload.model_dump(exclude_unset=True)
    changed = [k for k in data if k != "base_revision"]
    if "name" in data:
        name, key = _name(data["name"])
        _check_name_free(db, key, exclude=form.id)
        form.name, form.name_key = name, key
    if "description" in data:
        form.description = data["description"] or None
    mode = data.get("mode", form.mode)
    if "form_type" in data:
        form.form_type = data["form_type"]
    if "definition" in data or mode != form.mode:
        form.draft_definition = rules.normalize_definition(data.get("definition", rules.upgrade_definition(form.draft_definition, mode=form.mode)), mode=mode)
    form.mode = mode
    if "scope" in data or "tenant_ids" in data:
        scope = data.get("scope", form.scope)
        _set_assignments(db, form, scope, data.get("tenant_ids", _assigned(db, form.id)))
    _touch(form, actor)
    audit(db, actor, form, "updated", f"Saved the draft ({', '.join(changed) or 'no changes'})", detail={"changed": changed})
    db.commit()
    db.refresh(form)
    return form_dict(db, form, detail=True)


def assign_form(db: Session, actor: BlogActor, form_id: UUID, scope: str, tenant_ids: list[UUID] | None) -> dict:
    """Changing who gets the form takes effect at once; it is not part of a version."""
    form = get_form(db, form_id, lock=True)
    _set_assignments(db, form, scope, tenant_ids)
    form.updated_by = actor.id
    audit(db, actor, form, "assigned", f"Set the form to {scope}" + (f" for {len(tenant_ids or [])} business(es)" if scope == "selected" else ""),
          detail={"scope": scope, "tenant_ids": [str(t) for t in (tenant_ids or [])]})
    db.commit()
    db.refresh(form)
    return form_dict(db, form, detail=True)


# ---------------------------------------------------------------- review, submit, publish

def _field_map(definition: dict | None) -> dict:
    return {f["key"]: f for f in rules.fields_of(definition)} if definition else {}


def _comparable(field: dict) -> dict:
    return {k: v for k, v in field.items() if k not in ("order", "id")}


def changes_since(db: Session, form: BlogForm) -> dict:
    """What the draft changes compared with the published version posts use now: fields added, removed and changed, by key."""
    row = version_row(db, form.id, form.current_version) if form.current_version else None
    before, after = _field_map(rules.upgrade_definition(row.definition, mode=row.mode) if row else None), _field_map(rules.upgrade_definition(form.draft_definition, mode=form.mode))
    changed = []
    for key in before.keys() & after.keys():
        a, b = _comparable(before[key]), _comparable(after[key])
        diff = sorted(k for k in a.keys() | b.keys() if a.get(k) != b.get(k))
        if diff:
            changed.append({"key": key, "label": after[key]["label"], "changed": diff})
    return {"since_version": form.current_version or None, "added": sorted(after.keys() - before.keys()) if row else sorted(after), "removed": sorted(before.keys() - after.keys()),
            "changed": sorted(changed, key=lambda c: c["key"])}


def review_form(db: Session, form_id: UUID) -> dict:
    """Is the draft ready to publish? Every problem is listed, with what the draft changes. Nothing is saved."""
    form = get_form(db, form_id)
    problems: list[dict] = []
    try:
        definition = rules.normalize_definition(form.draft_definition, mode=form.mode)
    except Exception as exc:
        problems = list(getattr(exc, "extra", {}).get("errors") or [rules.err("definition", "INVALID_FORM", str(getattr(exc, "detail", exc)))])
        definition = rules.upgrade_definition(form.draft_definition, mode=form.mode)
    if form.scope == "selected" and not _assigned(db, form.id):
        problems.append(rules.err("tenant_ids", "REQUIRED", "Choose at least one business before you publish a form that is not global"))
    fields = rules.fields_of(definition)
    warnings = []
    if not form.has_unpublished_changes and form.current_version:
        warnings.append({"code": "NO_CHANGES", "message": "The draft is the same as the published version"})
    if form.mode == "configurable" and not any(f.get("kind") == "custom" for f in fields):
        warnings.append({"code": "NO_CUSTOM_FIELDS", "message": "The form has no custom fields: it is the same as the Standard form with some settings changed"})
    submitted = form.review_status == "submitted" and form.reviewed_revision == form.revision
    return {"form_id": form.id, "revision": form.revision, "ready": not problems, "problems": problems, "warnings": warnings, "changes": changes_since(db, form),
            "section_count": len(definition.get("sections", [])), "field_count": len(fields), "review_status": "submitted" if submitted else "draft",
            "review_required_to_publish": bool(settings.BLOG_FORM_REQUIRE_REVIEW)}


def submit_form(db: Session, actor: BlogActor, form_id: UUID, base_revision: int | None) -> dict:
    """The review checkpoint: the draft was checked and is ready to publish. Editing it again after this asks for a new submit."""
    form = get_form(db, form_id, lock=True)
    _check_revision(form, base_revision)
    result = review_form(db, form_id)
    if not result["ready"]:
        raise problem(422, "The draft has problems. Fix them before you submit it", "FORM_NOT_READY", errors=result["problems"])
    form.review_status, form.reviewed_revision, form.updated_by = "submitted", form.revision, actor.id
    audit(db, actor, form, "submitted", f"Submitted revision {form.revision} for publishing", detail={"changes": result["changes"]})
    db.commit()
    db.refresh(form)
    return form_dict(db, form, detail=True)


def publish_form(db: Session, actor: BlogActor, form_id: UUID, note: str | None, base_revision: int | None) -> dict:
    form = get_form(db, form_id, lock=True)
    _check_revision(form, base_revision)
    if form.scope == "selected" and not _assigned(db, form.id):
        raise problem(422, "Choose at least one business before you publish a form that is not global", "VALIDATION_FAILED",
                      errors=[rules.err("tenant_ids", "REQUIRED", "Choose at least one business")])
    if settings.BLOG_FORM_REQUIRE_REVIEW and not (form.review_status == "submitted" and form.reviewed_revision == form.revision):
        raise problem(409, "Submit the draft for review first (POST /admin/forms/{id}/submit). It has changed since it was last submitted", "REVIEW_REQUIRED")
    definition = rules.normalize_definition(form.draft_definition, mode=form.mode)
    form.current_version += 1
    db.add(BlogFormVersion(form_id=form.id, version=form.current_version, name=form.name, form_type=form.form_type, mode=form.mode,
                           definition=definition, note=(note or "").strip()[:500] or None, published_by=actor.id))
    first = form.status == "draft"
    form.status, form.has_unpublished_changes, form.updated_by = "published", False, actor.id
    form.review_status, form.reviewed_revision = "draft", None
    if first:
        form.is_active = True
    audit(db, actor, form, "published", f"Published version {form.current_version}" + (f": {note.strip()[:200]}" if (note or "").strip() else ""), version=form.current_version)
    db.commit()
    db.refresh(form)
    return form_dict(db, form, detail=True)


def retire_form(db: Session, actor: BlogActor, form_id: UUID) -> dict:
    """Withdraw a published form for good: not offered, cannot be activated. Posts written with it keep their version and stay readable. Only an inactive form can be retired."""
    form = get_form(db, form_id, lock=True)
    if form.status == "retired":
        raise problem(409, "This form is already retired", "INVALID_STATUS")
    if form.status != "published":
        raise problem(409, "Only a published form can be retired. Delete a draft instead", "INVALID_STATUS")
    if form.is_active:
        raise problem(409, "Deactivate the form before you retire it", "FORM_ACTIVE")
    form.status, form.updated_by = "retired", actor.id
    audit(db, actor, form, "retired", f"Retired the form (version {form.current_version})", version=form.current_version)
    db.commit()
    db.refresh(form)
    return form_dict(db, form, detail=True)


def restore_form(db: Session, actor: BlogActor, form_id: UUID) -> dict:
    """A retired form is published again, inactive: activate it to offer it."""
    form = get_form(db, form_id, lock=True)
    if form.status != "retired":
        raise problem(409, "Only a retired form can be restored", "INVALID_STATUS")
    form.status, form.updated_by = "published", actor.id
    audit(db, actor, form, "restored", f"Restored the form (version {form.current_version})", version=form.current_version)
    db.commit()
    db.refresh(form)
    return form_dict(db, form, detail=True)


def set_active(db: Session, actor: BlogActor, form_id: UUID, active: bool) -> dict:
    form = get_form(db, form_id, lock=True)
    if active and form.status == "retired":
        raise problem(409, "This form is retired. Restore it first", "FORM_RETIRED")
    if active and (form.status != "published" or form.current_version < 1):
        raise problem(409, "Publish the form before you activate it", "FORM_NOT_PUBLISHED")
    form.is_active, form.updated_by = active, actor.id
    audit(db, actor, form, "activated" if active else "deactivated", ("Activated" if active else "Deactivated") + f" the form (version {form.current_version})", version=form.current_version)
    db.commit()
    db.refresh(form)
    return form_dict(db, form, detail=True)


def delete_form(db: Session, form_id: UUID, actor: BlogActor | None = None) -> None:
    form = get_form(db, form_id, lock=True)
    used = _posts_using(db, form.id)
    if used:
        raise problem(409, f"{used} post(s) were written with this form, so it cannot be deleted. Deactivate it instead", "FORM_IN_USE", post_count=used)
    audit(db, actor, form, "deleted", f"Deleted the form '{form.name}'", detail={"versions": form.current_version})
    db.query(BlogFormAssignment).filter(BlogFormAssignment.form_id == form.id).delete(synchronize_session=False)
    db.query(BlogFormVersion).filter(BlogFormVersion.form_id == form.id).delete(synchronize_session=False)
    db.delete(form)
    db.commit()


# ---------------------------------------------------------------- the builder: sections and fields of the draft

def _builder_error(exc: ops.BuilderError):
    return problem(exc.status, exc.message, exc.code, **({"errors": exc.errors} if exc.errors else {}))


def build(db: Session, actor: BlogActor, form_id: UUID, base_revision: int | None, operation) -> dict:
    """Runs one builder operation on the draft. `operation(definition)` returns (new definition, summary, action, detail). The result must still be a valid
    blog form (title, body and category present and protected), the revision goes up, the review is withdrawn and the action is audited."""
    form = get_form(db, form_id, lock=True)
    _check_revision(form, base_revision)
    if form.mode == "standard":
        raise problem(409, "A Standard form uses the built-in fields and cannot be changed. Change its mode to configurable first", "STANDARD_FORM_FIXED")
    current = rules.upgrade_definition(form.draft_definition, mode=form.mode)
    try:
        new, summary, action, detail = operation(current)
    except ops.BuilderError as exc:
        raise _builder_error(exc)
    form.draft_definition = rules.normalize_definition(new, mode="configurable")
    _touch(form, actor)
    audit(db, actor, form, action, summary, detail=detail)
    db.commit()
    db.refresh(form)
    out = form_dict(db, form, detail=True)
    out["change"] = {"action": action, "summary": summary, **(detail or {})}
    return out


def _guard_custom_key(raw: dict) -> None:
    if str((raw or {}).get("type") or "").lower() in ("datetime", "toggle"):
        raise ops.BuilderError("VALIDATION_FAILED", "Blog forms do not have this field type", errors=[rules.err("field.type", "INVALID_TYPE", "datetime and toggle are for Event and Training forms")])
    key = str((raw or {}).get("key") or "")
    if key in rules.CORE_FIELDS:
        raise ops.BuilderError("KEY_RESERVED", f"'{key}' is one of the built-in blog fields. Edit that field instead of adding it", status=409,
                               errors=[rules.err("key", "KEY_RESERVED", f"'{key}' is a built-in field")])


def add_section(db, actor, form_id, base_revision, *, title, description, position, visibility="published"):
    return build(db, actor, form_id, base_revision, lambda d: (*ops.add_section(d, title=title, description=description, position=position, visibility=visibility), "section_added", None))


def edit_section(db, actor, form_id, base_revision, section_id, *, title, description, visibility=None, enabled=None):
    return build(db, actor, form_id, base_revision, lambda d: (*ops.edit_section(d, section_id, title=title, description=description, visibility=visibility, enabled=enabled), "section_edited", {"section_id": section_id}))


def delete_section(db, actor, form_id, base_revision, section_id, *, move_to):
    return build(db, actor, form_id, base_revision, lambda d: (*ops.delete_section(d, section_id, move_to=move_to), "section_deleted", {"section_id": section_id, "moved_to": move_to}))


def reorder_sections(db, actor, form_id, base_revision, ids):
    return build(db, actor, form_id, base_revision, lambda d: (*ops.reorder_sections(d, [str(i) for i in ids]), "sections_reordered", None))


def add_field(db, actor, form_id, base_revision, *, section_id, field, position):
    def operation(d):
        _guard_custom_key(field)
        new, added, summary = ops.add_field(d, section_id, field, position=position)
        return new, summary, "field_added", {"field_id": added["id"], "key": added["key"], "type": added["type"]}

    return build(db, actor, form_id, base_revision, operation)


def edit_field(db, actor, form_id, base_revision, field_ref, patch):
    def operation(d):
        new, edited, summary = ops.edit_field(d, field_ref, patch)
        return new, summary, "field_edited", {"field_id": edited["id"], "key": edited["key"], "changed": sorted(k for k in patch if k not in ("id",))}

    return build(db, actor, form_id, base_revision, operation)


def delete_field(db, actor, form_id, base_revision, field_ref):
    def operation(d):
        _section, field = ops._locate(d, field_ref)
        if field.get("kind") == "core" and not field.get("system"):
            raise ops.BuilderError("CORE_FIELD", f"'{field['key']}' is a built-in blog field. Switch it off (enabled: false) instead of deleting it", status=409,
                                   errors=[rules.err("field", "CORE_FIELD", "A built-in field cannot be deleted")])
        new, summary = ops.delete_field(d, field_ref)
        return new, summary, "field_deleted", {"field_id": field["id"], "key": field["key"]}

    return build(db, actor, form_id, base_revision, operation)


def duplicate_field(db, actor, form_id, base_revision, field_ref):
    def operation(d):
        new, clone, summary = ops.duplicate_field(d, field_ref)
        return new, summary, "field_duplicated", {"field_id": clone["id"], "key": clone["key"], "copied_from": field_ref}

    return build(db, actor, form_id, base_revision, operation)


def reorder_fields(db, actor, form_id, base_revision, section_id, ids):
    return build(db, actor, form_id, base_revision, lambda d: (*ops.reorder_fields(d, section_id, [str(i) for i in ids]), "fields_reordered", {"section_id": section_id}))


def move_field(db, actor, form_id, base_revision, field_ref, *, section_id, position):
    def operation(d):
        new, moved, summary = ops.move_field(d, field_ref, section_id, position=position)
        return new, summary, "field_moved", {"field_id": moved["id"], "key": moved["key"], "section_id": section_id}

    return build(db, actor, form_id, base_revision, operation)


def list_audit(db: Session, form_id: UUID, *, action: str | None, page: int, page_size: int) -> tuple[list, int]:
    query = db.query(BlogFormAudit).filter(BlogFormAudit.form_id == form_id)
    if action:
        query = query.filter(BlogFormAudit.action == action)
    total = query.count()
    rows = query.order_by(BlogFormAudit.created_at.desc(), BlogFormAudit.id).offset((page - 1) * page_size).limit(page_size).all()
    return rows, total


def public_form(db: Session, kind: str) -> dict:
    """What the public and mobile apps may know about the form of this kind: only enabled, published fields. Platform posts use global forms."""
    return resolve_active(db, kind, None).summary("public")


# ---------------------------------------------------------------- reading

def list_forms(db: Session, *, form_type: str | None, status: str | None, is_active: bool | None, scope: str | None, search: str | None,
               tenant_id: UUID | None, page: int, page_size: int) -> tuple[list[dict], int]:
    query = db.query(BlogForm)
    if form_type:
        query = query.filter(BlogForm.form_type == form_type)
    if status:
        query = query.filter(BlogForm.status == status)
    if is_active is not None:
        query = query.filter(BlogForm.is_active.is_(is_active))
    if scope:
        query = query.filter(BlogForm.scope == scope)
    if search and search.strip():
        query = query.filter(BlogForm.name.ilike(f"%{search.strip()}%"))
    if tenant_id:
        query = query.filter((BlogForm.scope == "global") | BlogForm.id.in_(db.query(BlogFormAssignment.form_id).filter(BlogFormAssignment.tenant_id == tenant_id)))
    total = query.count()
    rows = query.order_by(BlogForm.updated_at.desc(), BlogForm.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return [form_dict(db, f, detail=False) for f in rows], total


def get_form_dict(db: Session, form_id: UUID) -> dict:
    return form_dict(db, get_form(db, form_id), detail=True)


def list_versions(db: Session, form_id: UUID) -> list[dict]:
    get_form(db, form_id)
    rows = db.query(BlogFormVersion).filter(BlogFormVersion.form_id == form_id).order_by(BlogFormVersion.version.desc()).all()
    counts = dict(db.query(BlogPost.form_version, func.count(BlogPost.id)).filter(BlogPost.form_id == form_id).group_by(BlogPost.form_version).all())
    return [{"version": r.version, "name": r.name, "form_type": r.form_type, "mode": r.mode, "note": r.note, "published_by": r.published_by,
             "published_at": r.published_at, "post_count": int(counts.get(r.version, 0))} for r in rows]


def get_version_dict(db: Session, form_id: UUID, version: int) -> dict:
    form = get_form(db, form_id)
    row = version_row(db, form_id, version)
    if row is None:
        raise problem(404, "That version of the form does not exist", "FORM_VERSION_NOT_FOUND")
    return {"form_id": form.id, "version": row.version, "name": row.name, "form_type": row.form_type, "mode": row.mode, "note": row.note,
            "published_by": row.published_by, "published_at": row.published_at, "is_current": row.version == form.current_version,
            "definition": rules.upgrade_definition(row.definition, mode=row.mode)}


def preview(db: Session, form_id: UUID | None, definition, mode: str | None, name: str | None) -> dict:
    """What the writer would see: the normalized sections and fields, in order. For a saved form (its draft) or an unsaved definition."""
    if form_id is not None:
        form = get_form(db, form_id)
        source, mode, name, definition = "draft", form.mode, form.name, rules.normalize_definition(form.draft_definition, mode=form.mode)
    else:
        mode = mode or "configurable"
        source, name, definition = "unsaved", name or "Untitled form", rules.normalize_definition(definition, mode=mode)
    fields = rules.fields_of(definition)
    return {"source": source, "name": name, "mode": mode, "definition": definition, "field_count": len(fields),
            "required_fields": [f["key"] for f in fields if f["required"]],
            "enterprise_admin_view": projection.project_form(definition, "enterprise_admin"), "public_view": projection.project_form(definition, "public")}


def diagnostics(db: Session) -> dict:
    """Numbers for monitoring and for checking a migration or a rollback: forms by lifecycle, definitions still in the older format, posts pointing at a missing form version, files."""
    from collections import Counter

    from app.models.blog_model import BlogFile

    forms = db.query(BlogForm).all()
    by_lifecycle = Counter(lifecycle_of(f)["lifecycle"] for f in forms)
    older_drafts = sum(1 for f in forms if f.draft_definition and not rules.is_current_format(f.draft_definition))
    older_versions = sum(1 for v in db.query(BlogFormVersion).all() if v.definition and not rules.is_current_format(v.definition))
    stale = [str(f.id) for f in forms if f.has_unpublished_changes and f.status == "published" and f.updated_at and (datetime.utcnow() - f.updated_at).days >= 30]
    pinned = db.query(BlogPost.form_id, BlogPost.form_version).filter(BlogPost.form_id.isnot(None), BlogPost.form_version.isnot(None)).distinct().all()
    missing = sum(1 for fid, ver in pinned if version_row(db, fid, ver) is None)
    live = BlogFile.is_deleted.is_(False)
    by_scan = dict(db.query(BlogFile.scan_status, func.count(BlogFile.id)).filter(live).group_by(BlogFile.scan_status).all())
    by_purpose = dict(db.query(BlogFile.purpose, func.count(BlogFile.id)).filter(live).group_by(BlogFile.purpose).all())
    return {"generated_at": datetime.utcnow(), "forms": {"total": len(forms), "by_lifecycle": dict(by_lifecycle), "drafts_in_older_format": older_drafts, "versions_in_older_format": older_versions,
                                                         "published_with_unpublished_changes_for_30_days": stale},
            "posts": {"pinned_to_a_missing_form_version": missing},
            "files": {"total": sum(by_scan.values()), "by_scan_status": by_scan, "by_purpose": by_purpose}}


def active_for_tenant(db: Session, tenant_id: UUID | None) -> dict:
    out = {}
    for kind in ("blog", "news"):
        out[kind] = resolve_active(db, kind, tenant_id).summary("super_admin")
    names = _business_names(db, [tenant_id] if tenant_id else [])
    return {"tenant_id": tenant_id, "business_name": names.get(tenant_id) if tenant_id else None, "blog": out["blog"], "news": out["news"]}
