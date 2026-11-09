"""Event and Training forms in the common form contract: the builder's view, the Enterprise Admin's active form, and what a given event or training shows.

Read only, and nothing stored changes: the stored sections are converted by app/form_builder/legacy_sections.py and filtered by app/form_builder/projection.py.
"""
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.form_builder import legacy_sections, projection


@dataclass
class Spec:
    name: str                  # "event" | "training"
    registry: dict
    domain_required: object
    config_model: type
    version_model: type
    service: object
    assignment_model: type | None = None


def spec(module: str) -> Spec:
    if module == "event":
        from app.models.event_form_config_model import EventFormAssignment, EventFormConfiguration, EventFormConfigurationVersion
        from app.services import event_form_config_service as svc
        from app.services.event_form_registry import DOMAIN_REQUIRED_CORE_KEYS, REGISTRY_BY_KEY

        return Spec("event", REGISTRY_BY_KEY, DOMAIN_REQUIRED_CORE_KEYS, EventFormConfiguration, EventFormConfigurationVersion, svc, EventFormAssignment)
    from app.models.training_form_config_model import TrainingFormAssignment, TrainingFormConfiguration, TrainingFormConfigurationVersion
    from app.services import training_form_config_service as svc
    from app.services.training_form_registry import DOMAIN_REQUIRED_CORE_KEYS, REGISTRY_BY_KEY

    return Spec("training", REGISTRY_BY_KEY, DOMAIN_REQUIRED_CORE_KEYS, TrainingFormConfiguration, TrainingFormConfigurationVersion, svc, TrainingFormAssignment)


def _definition(sp: Spec, sections, *, include_disabled: bool) -> dict:
    return legacy_sections.to_common(sections, registry=sp.registry, domain_required=sp.domain_required, include_disabled=include_disabled)


def assignments_of(db: Session, sp: Spec, config) -> list[dict]:
    """The businesses a form is given to (a `selective` form), with their names. A global form has none."""
    from app.models.enterprise_model import Enterprise

    rows = db.query(sp.assignment_model).filter(sp.assignment_model.configuration_id == config.id).all() if sp.assignment_model is not None else []
    names = {}
    ids = [r.enterprise_id for r in rows if r.enterprise_id]
    if ids:
        names = {e.id: (e.business_short_name or e.business_legal_name) for e in db.query(Enterprise).filter(Enterprise.id.in_(ids)).all()}
    return [{"tenant_id": r.tenant_id, "enterprise_id": r.enterprise_id, "business_name": names.get(r.enterprise_id)} for r in rows]


def public_active_view(db: Session, module: str, enterprise_id: UUID | None) -> dict:
    """The active published form for public and mobile apps: the form assigned to the business if there is one, else the active global form. Only enabled `published` fields."""
    from uuid import uuid4

    from app.models.enterprise_model import Enterprise

    sp = spec(module)
    tenant_id = uuid4()                                           # no business: nothing is assigned, so the global form is the answer
    if enterprise_id is not None:
        enterprise = db.get(Enterprise, enterprise_id)
        if enterprise is None:
            raise HTTPException(status_code=404, detail="Enterprise not found")
        tenant_id = enterprise.tenant_id
    resolved = sp.service._active_config_for_tenant(db, tenant_id)
    if resolved is None and module == "event":
        resolved = sp.service._active_legacy_config_version(db)
    if resolved is None:
        raise HTTPException(status_code=404, detail="There is no active published form")
    config, version = resolved
    definition = _definition(sp, version.sections, include_disabled=False)
    return {"configuration_id": config.id, "version_id": version.id, "name": config.name, "description": config.description, "scope": config.scope, "version": version.version,
            "published_at": version.published_at, "audience": "public", "definition": projection.project_form(definition, "public")}


def builder_view(db: Session, module: str, config_id: UUID, version_id: UUID | None = None) -> dict:
    """The Super Admin's form builder view: the draft (or the version asked for, or the latest published), every field including switched-off ones, and protected flags."""
    sp = spec(module)
    config = sp.service._get_config_or_404(db, config_id)
    if version_id is not None:
        version = db.query(sp.version_model).filter(sp.version_model.id == version_id, sp.version_model.configuration_id == config.id).first()
    else:
        version = sp.service._get_draft_version(db, config.id) or sp.service._get_published_version(db, config.id)
    if version is None:
        raise HTTPException(status_code=404, detail="That version of the form does not exist")
    definition = _definition(sp, version.sections, include_disabled=True)
    from app.services.form_common_write import content_hash

    current = content_hash(version.sections)
    from app.core.config import settings
    from app.form_builder.lifecycle import lifecycle as lifecycle_of

    submitted = getattr(version, "reviewed_hash", None) == current and version.status == "draft"
    has_published = db.query(sp.version_model).filter(sp.version_model.configuration_id == config.id, sp.version_model.status == "published").count() > 0
    state = lifecycle_of(status="retired" if config.status == "retired" else ("published" if has_published else "draft"), is_active=bool(config.is_active), submitted=submitted,
                         has_unpublished_changes=version.status == "draft" and has_published, require_review=bool(settings.FORM_REQUIRE_REVIEW))
    return {**state, "etag": current, "review_status": "submitted" if submitted else "draft", "description": config.description, "assignments": assignments_of(db, sp, config),
            "configuration_id": config.id, "name": config.name, "scope": config.scope, "status": config.status, "is_active": bool(config.is_active),
            "version_id": version.id, "version": version.version, "version_status": version.status, "audience": "super_admin",
            "definition": projection.project_form(definition, "super_admin"), "field_count": sum(len(s["fields"]) for s in definition["sections"])}


def active_view(db: Session, module: str, current_user: dict, access_token: str | None) -> dict:
    """The form the signed-in Enterprise Admin writes with, as the Enterprise Admin's view: enabled fields only, with rules, placeholders and the required flag."""
    sp = spec(module)
    active = sp.service.get_active_form_configuration_service(db, current_user, access_token=access_token)
    definition = _definition(sp, active.get("sections"), include_disabled=False)
    return {"configuration_id": active.get("configuration_id"), "version_id": active.get("version_id"), "name": active.get("name"), "scope": active.get("scope"),
            "version": active.get("version"), "audience": "enterprise_admin", "definition": projection.project_form(definition, "enterprise_admin")}


def _sections_of(sp: Spec, db: Session, record, current_user: dict | None) -> tuple[object, object, list]:
    version = db.get(sp.version_model, record.form_configuration_version_id) if getattr(record, "form_configuration_version_id", None) else None
    if version is not None:
        return db.get(sp.config_model, version.configuration_id), version, version.sections or []
    if sp.name == "event":
        config, version = sp.service._legacy_config_version(db)       # an event made before forms had versions: the platform's default form
        return config, version, version.sections or []
    return None, None, []


def record_view(db: Session, module: str, record, audience: str, current_user: dict | None = None) -> dict:
    """The form an event or training was written with, and its custom values, for one kind of caller. Public: only enabled published fields and their values;
    an Enterprise Admin only field, a switched-off field and its values are not in it."""
    sp = spec(module)
    config, version, sections = _sections_of(sp, db, record, current_user)
    definition = _definition(sp, sections, include_disabled=audience == "super_admin")
    values = legacy_sections.values_by_key(definition, getattr(record, "custom_values", None))
    if audience != "public":                         # the values of Enterprise Admin only fields are kept apart and only these audiences read them
        values.update(legacy_sections.values_by_key(definition, getattr(record, "private_custom_values", None)))
    return {f"{module}_id": record.id, "audience": audience, "configuration_id": config.id if config else None, "version_id": version.id if version else None,
            "version": version.version if version else None, "definition": projection.project_form(definition, audience),
            "custom_fields": projection.project_values(definition, values, audience)}
