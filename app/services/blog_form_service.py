"""Blog / news forms: Super Admin builds them, publishes versions, assigns them; a post is written against one version.

A form has a draft definition (what the Super Admin is editing) and published versions (frozen). Publishing a form copies the draft to
a new version. `is_active` says whether new posts may use it. A post keeps the version it was published with, so changing a form never
changes a live post; a post that is not live follows the active form again each time it is saved.

Which form a business gets for a kind of post: among forms that are active, published and of that kind (or "both"), one assigned to
the business ("selected") beats a global one; if several are left, the one published most recently wins. With none, the built-in
Standard form applies (version 0, no form id). Platform posts (no business) only use global forms.
"""
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.blog_access import BlogActor
from app.exceptions.problem import problem
from app.models.blog_model import BlogForm, BlogFormAssignment, BlogFormVersion, BlogPost
from app.services import blog_form_rules as rules

FORM_TYPES = ("blog", "news", "both")


@dataclass
class ResolvedForm:
    form_id: UUID | None
    version: int
    name: str
    mode: str
    form_type: str
    definition: dict

    def summary(self) -> dict:
        return {"form_id": self.form_id, "version": self.version, "name": self.name, "mode": self.mode, "form_type": self.form_type,
                "definition": self.definition}


BUILT_IN = "Standard"


def built_in() -> ResolvedForm:
    return ResolvedForm(None, 0, BUILT_IN, "standard", "both", rules.standard_definition())


def _now() -> datetime:
    return datetime.utcnow()


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
    return ResolvedForm(form.id, row.version, form.name, form.mode, form.form_type, row.definition)


def resolve_pinned(db: Session, form_id: UUID | None, version: int | None) -> ResolvedForm:
    """The exact form version a post was written with. If it cannot be found, the built-in form."""
    if form_id is None or not version:
        return built_in()
    row = version_row(db, form_id, version)
    form = db.get(BlogForm, form_id)
    if row is None or form is None:
        return built_in()
    return ResolvedForm(form.id, row.version, row.name, row.mode, row.form_type, row.definition)


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


def form_dict(db: Session, form: BlogForm, *, detail: bool) -> dict:
    tenants = _assigned(db, form.id) if form.scope == "selected" else []
    out = {
        "id": form.id, "name": form.name, "description": form.description, "form_type": form.form_type, "mode": form.mode, "scope": form.scope,
        "status": form.status, "is_active": bool(form.is_active), "current_version": form.current_version, "revision": form.revision,
        "has_unpublished_changes": bool(form.has_unpublished_changes), "assigned_count": len(tenants), "post_count": _posts_using(db, form.id),
        "created_by": form.created_by, "updated_by": form.updated_by, "created_at": form.created_at, "updated_at": form.updated_at,
    }
    if detail:
        names = _business_names(db, tenants)
        out["assignments"] = [{"tenant_id": t, "business_name": names.get(t)} for t in tenants]
        out["definition"] = form.draft_definition
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
    db.commit()
    db.refresh(form)
    return form_dict(db, form, detail=True)


def update_form(db: Session, actor: BlogActor, form_id: UUID, payload) -> dict:
    """Save the draft. Posts are not affected until the form is published."""
    form = get_form(db, form_id, lock=True)
    _check_revision(form, payload.base_revision)
    data = payload.model_dump(exclude_unset=True)
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
        form.draft_definition = rules.normalize_definition(data.get("definition", form.draft_definition), mode=mode)
    form.mode = mode
    if "scope" in data or "tenant_ids" in data:
        scope = data.get("scope", form.scope)
        _set_assignments(db, form, scope, data.get("tenant_ids", _assigned(db, form.id)))
    form.revision += 1
    form.has_unpublished_changes = True
    form.updated_by = actor.id
    db.commit()
    db.refresh(form)
    return form_dict(db, form, detail=True)


def assign_form(db: Session, actor: BlogActor, form_id: UUID, scope: str, tenant_ids: list[UUID] | None) -> dict:
    """Changing who gets the form takes effect at once; it is not part of a version."""
    form = get_form(db, form_id, lock=True)
    _set_assignments(db, form, scope, tenant_ids)
    form.updated_by = actor.id
    db.commit()
    db.refresh(form)
    return form_dict(db, form, detail=True)


def publish_form(db: Session, actor: BlogActor, form_id: UUID, note: str | None, base_revision: int | None) -> dict:
    form = get_form(db, form_id, lock=True)
    _check_revision(form, base_revision)
    if form.scope == "selected" and not _assigned(db, form.id):
        raise problem(422, "Choose at least one business before you publish a form that is not global", "VALIDATION_FAILED",
                      errors=[rules.err("tenant_ids", "REQUIRED", "Choose at least one business")])
    definition = rules.normalize_definition(form.draft_definition, mode=form.mode)
    form.current_version += 1
    db.add(BlogFormVersion(form_id=form.id, version=form.current_version, name=form.name, form_type=form.form_type, mode=form.mode,
                           definition=definition, note=(note or "").strip()[:500] or None, published_by=actor.id))
    first = form.status == "draft"
    form.status, form.has_unpublished_changes, form.updated_by = "published", False, actor.id
    if first:
        form.is_active = True
    db.commit()
    db.refresh(form)
    return form_dict(db, form, detail=True)


def set_active(db: Session, actor: BlogActor, form_id: UUID, active: bool) -> dict:
    form = get_form(db, form_id, lock=True)
    if active and (form.status != "published" or form.current_version < 1):
        raise problem(409, "Publish the form before you activate it", "FORM_NOT_PUBLISHED")
    form.is_active, form.updated_by = active, actor.id
    db.commit()
    db.refresh(form)
    return form_dict(db, form, detail=True)


def delete_form(db: Session, form_id: UUID) -> None:
    form = get_form(db, form_id, lock=True)
    used = _posts_using(db, form.id)
    if used:
        raise problem(409, f"{used} post(s) were written with this form, so it cannot be deleted. Deactivate it instead", "FORM_IN_USE", post_count=used)
    db.query(BlogFormAssignment).filter(BlogFormAssignment.form_id == form.id).delete(synchronize_session=False)
    db.query(BlogFormVersion).filter(BlogFormVersion.form_id == form.id).delete(synchronize_session=False)
    db.delete(form)
    db.commit()


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
            "definition": row.definition}


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
            "required_fields": [f["key"] for f in fields if f["required"]]}


def active_for_tenant(db: Session, tenant_id: UUID | None) -> dict:
    out = {}
    for kind in ("blog", "news"):
        out[kind] = resolve_active(db, kind, tenant_id).summary()
    names = _business_names(db, [tenant_id] if tenant_id else [])
    return {"tenant_id": tenant_id, "business_name": names.get(tenant_id) if tenant_id else None, "blog": out["blog"], "news": out["news"]}
