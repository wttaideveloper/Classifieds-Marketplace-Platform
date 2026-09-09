"""Training Form Configuration — Super Admin builder and runtime resolution."""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.auth_context import resolve_auth_tenant_id_with_db
from app.models.enterprise_model import Enterprise
from app.models.training_form_config_model import (
    TrainingFormAssignment,
    TrainingFormAudit,
    TrainingFormConfiguration,
    TrainingFormConfigurationVersion,
)
from app.services.invigorate_auth_client import resolve_tenant_ids_from_slugs
from app.services.training_form_registry import (
    CUSTOM_RENDERERS,
    DEFAULT_CONFIGURATION_ID,
    DEFAULT_VERSION_ID,
    DOMAIN_REQUIRED_CORE_KEYS,
    REGISTRY_BY_KEY,
    build_default_sections,
    build_seed_sections,
    normalize_sections,
    validate_sections_for_publish,
)


def _actor(current_user: dict | None) -> str | None:
    if not current_user:
        return None
    return str(current_user.get("id") or current_user.get("email") or "")


def _audit(db: Session, *, configuration_id, version_id, action: str, actor_id, before=None, after=None):
    db.add(
        TrainingFormAudit(
            configuration_id=configuration_id,
            version_id=version_id,
            actor_id=actor_id,
            action=action,
            before=before,
            after=after,
        )
    )


def _version_to_response(version: TrainingFormConfigurationVersion) -> dict:
    return {
        "id": version.id,
        "configuration_id": version.configuration_id,
        "version": version.version,
        "status": version.status,
        "sections": normalize_sections(version.sections or [], assign_ids=False),
        "created_by": version.created_by,
        "created_at": version.created_at,
        "published_at": version.published_at,
    }


def _config_summary(config: TrainingFormConfiguration) -> dict:
    return {
        "id": config.id,
        "name": config.name,
        "description": config.description,
        "scope": config.scope,
        "is_global": config.scope == "global",
        "status": config.status,
        "is_active": config.is_active,
        "current_version": config.current_version,
        "created_by": config.created_by,
        "created_at": config.created_at,
        "updated_at": config.updated_at,
        "published_at": config.published_at,
    }


def _get_config_or_404(db: Session, config_id: UUID) -> TrainingFormConfiguration:
    config = db.query(TrainingFormConfiguration).filter(TrainingFormConfiguration.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="Training form configuration not found")
    return config


def _get_draft_version(db: Session, config_id: UUID) -> TrainingFormConfigurationVersion | None:
    return (
        db.query(TrainingFormConfigurationVersion)
        .filter(
            TrainingFormConfigurationVersion.configuration_id == config_id,
            TrainingFormConfigurationVersion.status == "draft",
        )
        .order_by(TrainingFormConfigurationVersion.version.desc())
        .first()
    )


def _get_published_version(db: Session, config_id: UUID, version_num: int | None = None) -> TrainingFormConfigurationVersion | None:
    q = db.query(TrainingFormConfigurationVersion).filter(
        TrainingFormConfigurationVersion.configuration_id == config_id,
        TrainingFormConfigurationVersion.status == "published",
    )
    if version_num is not None:
        q = q.filter(TrainingFormConfigurationVersion.version == version_num)
    return q.order_by(TrainingFormConfigurationVersion.version.desc()).first()


def list_configurations_service(
    db: Session,
    *,
    status: str | None = None,
    search: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> list[dict]:
    q = db.query(TrainingFormConfiguration)
    if status:
        if status == "active":
            q = q.filter(TrainingFormConfiguration.is_active.is_(True))
        elif status == "archived":
            q = q.filter(TrainingFormConfiguration.status == "retired")
        else:
            q = q.filter(TrainingFormConfiguration.status == status)
    if search:
        term = f"%{search.strip()}%"
        q = q.filter(TrainingFormConfiguration.name.ilike(term))
    q = q.order_by(TrainingFormConfiguration.created_at.desc())
    rows = q.all()
    if page and page_size:
        start = (max(page, 1) - 1) * page_size
        rows = rows[start : start + page_size]
    return [_config_summary(r) for r in rows]


def get_configuration_service(db: Session, config_id: UUID) -> dict:
    config = _get_config_or_404(db, config_id)
    out = _config_summary(config)
    draft = _get_draft_version(db, config_id)
    published = _get_published_version(db, config_id)
    out["draft_version"] = _version_to_response(draft) if draft else None
    out["published_version"] = _version_to_response(published) if published else None
    return out


def create_configuration_service(db: Session, payload, current_user: dict) -> dict:
    scope = payload.scope or "global"
    if scope not in ("global", "selective"):
        raise HTTPException(status_code=400, detail="scope must be global|selective")
    sections = normalize_sections([s.model_dump() for s in payload.sections] if payload.sections else build_default_sections())
    config = TrainingFormConfiguration(
        name=payload.name,
        description=payload.description,
        scope=scope,
        status="draft",
        is_active=False,
        current_version=1,
        created_by=_actor(current_user),
    )
    db.add(config)
    db.flush()
    version = TrainingFormConfigurationVersion(
        configuration_id=config.id,
        version=1,
        status="draft",
        sections=sections,
        created_by=_actor(current_user),
    )
    db.add(version)
    _audit(db, configuration_id=config.id, version_id=version.id, action="configuration_created", actor_id=_actor(current_user), after={"name": config.name, "scope": scope})
    db.commit()
    db.refresh(config)
    db.refresh(version)
    return {**_config_summary(config), "draft_version": _version_to_response(version)}


def update_configuration_service(db: Session, config_id: UUID, payload, current_user: dict) -> dict:
    config = _get_config_or_404(db, config_id)
    draft = _get_draft_version(db, config_id)
    if config.status == "published" and not draft:
        published = _get_published_version(db, config_id)
        if not published:
            raise HTTPException(status_code=400, detail="No published version to fork from")
        new_version_num = config.current_version + 1
        draft = TrainingFormConfigurationVersion(
            configuration_id=config.id,
            version=new_version_num,
            status="draft",
            sections=deepcopy(published.sections or []),
            created_by=_actor(current_user),
        )
        db.add(draft)
        config.current_version = new_version_num
        config.status = "draft"
    elif not draft:
        draft = TrainingFormConfigurationVersion(
            configuration_id=config.id,
            version=config.current_version or 1,
            status="draft",
            sections=[],
            created_by=_actor(current_user),
        )
        db.add(draft)

    before = {"name": config.name, "sections_count": len(draft.sections or [])}
    if payload.name is not None:
        config.name = payload.name
    if payload.description is not None:
        config.description = payload.description
    if payload.sections is not None:
        draft.sections = normalize_sections([s.model_dump() for s in payload.sections])
    config.updated_at = datetime.utcnow()
    _audit(db, configuration_id=config.id, version_id=draft.id, action="draft_changed", actor_id=_actor(current_user), before=before, after={"name": config.name})
    db.commit()
    return get_configuration_service(db, config_id)


def delete_configuration_service(db: Session, config_id: UUID, current_user: dict) -> dict:
    config = _get_config_or_404(db, config_id)
    if str(config.id) == DEFAULT_CONFIGURATION_ID:
        raise HTTPException(status_code=400, detail="Default Training form configuration cannot be deleted")
    if config.status == "published":
        raise HTTPException(status_code=400, detail="Published configurations must be retired, not deleted")
    from app.models.training_model import Training

    referenced = db.query(Training).filter(Training.form_configuration_id == config_id).count()
    if referenced:
        raise HTTPException(status_code=400, detail="Configuration referenced by Trainings — retire instead")
    db.delete(config)
    db.commit()
    return {"message": "Configuration deleted"}


def publish_configuration_service(db: Session, config_id: UUID, current_user: dict) -> dict:
    config = _get_config_or_404(db, config_id)
    draft = _get_draft_version(db, config_id)
    if not draft:
        raise HTTPException(status_code=400, detail="No draft version to publish")
    sections = normalize_sections(draft.sections or [], assign_ids=False)
    validate_sections_for_publish(sections, scope=config.scope)

    if config.scope == "global" and config.is_active:
        other = (
            db.query(TrainingFormConfiguration)
            .filter(
                TrainingFormConfiguration.scope == "global",
                TrainingFormConfiguration.is_active.is_(True),
                TrainingFormConfiguration.id != config_id,
                TrainingFormConfiguration.status == "published",
            )
            .first()
        )
        if other:
            raise HTTPException(status_code=400, detail=f"Another active global configuration exists: {other.name}")

    now = datetime.utcnow()
    draft.status = "published"
    draft.published_at = now
    draft.sections = sections
    config.status = "published"
    config.published_at = now
    config.current_version = draft.version
    _audit(db, configuration_id=config.id, version_id=draft.id, action="configuration_published", actor_id=_actor(current_user), after={"version": draft.version})
    db.commit()
    return {
        "configuration_id": config.id,
        "version_id": draft.id,
        "version": draft.version,
        "status": "published",
        "published_at": now,
    }


def activate_configuration_service(db: Session, config_id: UUID, current_user: dict) -> dict:
    config = _get_config_or_404(db, config_id)
    if config.status != "published":
        raise HTTPException(status_code=400, detail="Only published configurations can be activated")
    published = _get_published_version(db, config_id)
    if not published:
        raise HTTPException(status_code=400, detail="No published version found")

    if config.scope == "global":
        others = (
            db.query(TrainingFormConfiguration)
            .filter(
                TrainingFormConfiguration.scope == "global",
                TrainingFormConfiguration.is_active.is_(True),
                TrainingFormConfiguration.id != config_id,
            )
            .all()
        )
        for other in others:
            other.is_active = False
            _audit(db, configuration_id=other.id, version_id=None, action="deactivated", actor_id=_actor(current_user), after={"reason": "replaced_by_global_activation"})

    if config.scope == "selective":
        tenant_ids = [a.tenant_id for a in db.query(TrainingFormAssignment).filter(TrainingFormAssignment.configuration_id == config_id).all()]
        for tid in tenant_ids:
            conflicts = (
                db.query(TrainingFormAssignment)
                .join(TrainingFormConfiguration, TrainingFormConfiguration.id == TrainingFormAssignment.configuration_id)
                .filter(
                    TrainingFormAssignment.tenant_id == tid,
                    TrainingFormAssignment.configuration_id != config_id,
                    TrainingFormConfiguration.is_active.is_(True),
                    TrainingFormConfiguration.scope == "selective",
                )
                .all()
            )
            for conflict in conflicts:
                other_config = db.query(TrainingFormConfiguration).filter(TrainingFormConfiguration.id == conflict.configuration_id).first()
                if other_config:
                    other_config.is_active = False

    config.is_active = True
    _audit(db, configuration_id=config.id, version_id=published.id, action="activated", actor_id=_actor(current_user))
    db.commit()
    return {"id": config.id, "is_active": True, "status": config.status}


def deactivate_configuration_service(db: Session, config_id: UUID, current_user: dict) -> dict:
    config = _get_config_or_404(db, config_id)
    config.is_active = False
    _audit(db, configuration_id=config.id, version_id=None, action="deactivated", actor_id=_actor(current_user))
    db.commit()
    return {"id": config.id, "is_active": False, "status": config.status}


def retire_configuration_service(db: Session, config_id: UUID, current_user: dict) -> dict:
    config = _get_config_or_404(db, config_id)
    if str(config.id) == DEFAULT_CONFIGURATION_ID:
        raise HTTPException(status_code=400, detail="Default Training form configuration cannot be retired")
    config.status = "retired"
    config.is_active = False
    _audit(db, configuration_id=config.id, version_id=None, action="retired", actor_id=_actor(current_user))
    db.commit()
    return {"id": config.id, "status": "retired", "is_active": False}


def list_versions_service(db: Session, config_id: UUID) -> list[dict]:
    _get_config_or_404(db, config_id)
    rows = (
        db.query(TrainingFormConfigurationVersion)
        .filter(TrainingFormConfigurationVersion.configuration_id == config_id)
        .order_by(TrainingFormConfigurationVersion.version.desc())
        .all()
    )
    return [_version_to_response(r) for r in rows]


def get_version_service(db: Session, config_id: UUID, version_id: UUID) -> dict:
    _get_config_or_404(db, config_id)
    version = (
        db.query(TrainingFormConfigurationVersion)
        .filter(
            TrainingFormConfigurationVersion.configuration_id == config_id,
            TrainingFormConfigurationVersion.id == version_id,
        )
        .first()
    )
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    return _version_to_response(version)


def list_assignments_service(db: Session, config_id: UUID) -> dict:
    config = _get_config_or_404(db, config_id)
    rows = db.query(TrainingFormAssignment).filter(TrainingFormAssignment.configuration_id == config_id).all()
    return {
        "configuration_id": config_id,
        "scope": config.scope,
        "is_global": config.scope == "global",
        "assignments": [{"tenant_id": r.tenant_id, "enterprise_id": r.enterprise_id} for r in rows],
    }


def _resolve_enterprise_for_tenant(db: Session, tenant_id: UUID) -> Enterprise | None:
    return (
        db.query(Enterprise)
        .filter(Enterprise.tenant_id == tenant_id, Enterprise.is_deleted.is_(False))
        .order_by(Enterprise.created_at.asc())
        .first()
    )


def _verify_enterprise_tenant(db: Session, enterprise_id: UUID, tenant_id: UUID) -> Enterprise:
    enterprise = db.query(Enterprise).filter(Enterprise.id == enterprise_id, Enterprise.is_deleted.is_(False)).first()
    if not enterprise:
        raise HTTPException(status_code=404, detail=f"Enterprise {enterprise_id} not found")
    if enterprise.tenant_id and str(enterprise.tenant_id) != str(tenant_id):
        raise HTTPException(status_code=400, detail=f"Enterprise {enterprise_id} does not belong to tenant {tenant_id}")
    return enterprise


def put_assignments_service(db: Session, config_id: UUID, payload, current_user: dict) -> dict:
    """Replace assignments.

    Frontend contract: `{enterprise_ids: []}` → make config global (clear assignments).
    Non-empty enterprise_ids → selective assignments.
    """
    config = _get_config_or_404(db, config_id)

    # Empty enterprise_ids list, or is_global: true ⇒ global
    if payload.is_global is True or (payload.enterprise_ids is not None and len(payload.enterprise_ids) == 0):
        existing = db.query(TrainingFormAssignment).filter(TrainingFormAssignment.configuration_id == config_id).all()
        for row in existing:
            db.delete(row)
            _audit(db, configuration_id=config_id, version_id=None, action="tenant_removed", actor_id=_actor(current_user), before={"tenant_id": str(row.tenant_id)})
        config.scope = "global"
        _audit(db, configuration_id=config_id, version_id=None, action="scope_changed", actor_id=_actor(current_user), after={"scope": "global"})
        db.commit()
        return list_assignments_service(db, config_id)

    targets: list[tuple[UUID, UUID | None]] = []
    if payload.assignments:
        for item in payload.assignments:
            tid = item.tenant_id
            eid = item.enterprise_id
            if eid:
                _verify_enterprise_tenant(db, eid, tid)
            elif not _resolve_enterprise_for_tenant(db, tid):
                raise HTTPException(status_code=404, detail=f"No enterprise linked to tenant {tid}")
            ent = _resolve_enterprise_for_tenant(db, tid)
            targets.append((tid, eid or (ent.id if ent else None)))
    elif payload.tenant_ids:
        for tid in payload.tenant_ids:
            ent = _resolve_enterprise_for_tenant(db, tid)
            if not ent:
                raise HTTPException(status_code=404, detail=f"No enterprise linked to tenant {tid}")
            targets.append((tid, ent.id))
    elif payload.tenant_slugs:
        for tid in resolve_tenant_ids_from_slugs(payload.tenant_slugs):
            ent = _resolve_enterprise_for_tenant(db, tid)
            if not ent:
                raise HTTPException(status_code=404, detail=f"No enterprise linked to tenant {tid}")
            targets.append((tid, ent.id))
    elif payload.enterprise_ids:
        for eid in payload.enterprise_ids:
            ent = db.query(Enterprise).filter(Enterprise.id == eid, Enterprise.is_deleted.is_(False)).first()
            if not ent or not ent.tenant_id:
                raise HTTPException(status_code=400, detail=f"Enterprise {eid} has no tenant_id")
            targets.append((ent.tenant_id, eid))
    else:
        raise HTTPException(status_code=400, detail="Provide tenant_ids, tenant_slugs, enterprise_ids, assignments, or is_global")

    config.scope = "selective"
    new_tenant_ids = {t[0] for t in targets}
    existing = db.query(TrainingFormAssignment).filter(TrainingFormAssignment.configuration_id == config_id).all()
    for row in existing:
        if row.tenant_id not in new_tenant_ids:
            db.delete(row)
            _audit(db, configuration_id=config_id, version_id=None, action="tenant_removed", actor_id=_actor(current_user), before={"tenant_id": str(row.tenant_id)})
    db.flush()

    for tid, eid in targets:
        conflict = db.query(TrainingFormAssignment).filter(TrainingFormAssignment.tenant_id == tid).first()
        if conflict and conflict.configuration_id != config_id:
            other = db.query(TrainingFormConfiguration).filter(TrainingFormConfiguration.id == conflict.configuration_id).first()
            if other and other.is_active:
                raise HTTPException(
                    status_code=409,
                    detail=f"Tenant {tid} already assigned to active configuration '{other.name}'. Deactivate or reassign first.",
                )
            db.delete(conflict)
            db.flush()
        existing_same = (
            db.query(TrainingFormAssignment)
            .filter(TrainingFormAssignment.configuration_id == config_id, TrainingFormAssignment.tenant_id == tid)
            .first()
        )
        if existing_same:
            existing_same.enterprise_id = eid
        else:
            db.add(TrainingFormAssignment(configuration_id=config_id, tenant_id=tid, enterprise_id=eid, created_by=_actor(current_user)))
            _audit(db, configuration_id=config_id, version_id=None, action="tenant_assigned", actor_id=_actor(current_user), after={"tenant_id": str(tid)})

    db.commit()
    return list_assignments_service(db, config_id)


def list_audit_service(db: Session, config_id: UUID) -> list[dict]:
    _get_config_or_404(db, config_id)
    rows = (
        db.query(TrainingFormAudit)
        .filter(TrainingFormAudit.configuration_id == config_id)
        .order_by(TrainingFormAudit.created_at.desc())
        .limit(200)
        .all()
    )
    return [
        {
            "id": r.id,
            "configuration_id": r.configuration_id,
            "version_id": r.version_id,
            "actor_id": r.actor_id,
            "action": r.action,
            "before": r.before,
            "after": r.after,
            "created_at": r.created_at,
        }
        for r in rows
    ]


def _active_config_for_tenant(db: Session, tenant_id: UUID) -> tuple[TrainingFormConfiguration, TrainingFormConfigurationVersion] | None:
    assignment = db.query(TrainingFormAssignment).filter(TrainingFormAssignment.tenant_id == tenant_id).first()
    if assignment:
        config = db.query(TrainingFormConfiguration).filter(
            TrainingFormConfiguration.id == assignment.configuration_id,
            TrainingFormConfiguration.is_active.is_(True),
            TrainingFormConfiguration.status == "published",
            TrainingFormConfiguration.scope == "selective",
        ).first()
        if config:
            version = _get_published_version(db, config.id)
            if version:
                return config, version

    global_config = (
        db.query(TrainingFormConfiguration)
        .filter(
            TrainingFormConfiguration.scope == "global",
            TrainingFormConfiguration.is_active.is_(True),
            TrainingFormConfiguration.status == "published",
        )
        .order_by(TrainingFormConfiguration.published_at.desc().nullslast())
        .first()
    )
    if global_config:
        version = _get_published_version(db, global_config.id)
        if version:
            return global_config, version
    return None


def build_active_response(config: TrainingFormConfiguration, version: TrainingFormConfigurationVersion) -> dict:
    sections = normalize_sections(version.sections or [], assign_ids=False)
    return {
        "configuration_id": config.id,
        "version_id": version.id,
        "name": config.name,
        "scope": config.scope,
        "is_global": config.scope == "global",
        "version": version.version,
        "sections": sections,
        "configuration_version": f"{config.id}:{version.version}",
    }


def get_active_form_configuration_service(db: Session, current_user: dict, access_token: str | None = None) -> dict:
    if current_user.get("role") not in ("admin", "super_admin", "provider"):
        raise HTTPException(status_code=403, detail="Enterprise Admin access required")

    tenant_raw = resolve_auth_tenant_id_with_db(db, current_user, access_token=access_token)
    tenant_uuid = UUID(str(tenant_raw)) if tenant_raw else None

    resolved = None
    if tenant_uuid is not None:
        resolved = _active_config_for_tenant(db, tenant_uuid)
    else:
        global_config = (
            db.query(TrainingFormConfiguration)
            .filter(
                TrainingFormConfiguration.scope == "global",
                TrainingFormConfiguration.is_active.is_(True),
                TrainingFormConfiguration.status == "published",
            )
            .order_by(TrainingFormConfiguration.published_at.desc().nullslast())
            .first()
        )
        if global_config:
            version = _get_published_version(db, global_config.id)
            if version:
                resolved = (global_config, version)

    if not resolved:
        raise HTTPException(status_code=404, detail="No active Training form configuration found")
    config, version = resolved
    return build_active_response(config, version)


def get_training_form_configuration_service(db: Session, training_id: UUID, current_user: dict) -> dict:
    from app.repository.training_repo import get_training_by_id

    training = get_training_by_id(db, training_id)
    if not training:
        raise HTTPException(status_code=404, detail="Training not found")
    if training.form_configuration_version_id:
        version = (
            db.query(TrainingFormConfigurationVersion)
            .filter(TrainingFormConfigurationVersion.id == training.form_configuration_version_id)
            .first()
        )
        if version:
            config = db.query(TrainingFormConfiguration).filter(TrainingFormConfiguration.id == version.configuration_id).first()
            if config:
                return build_active_response(config, version)
    # Fall back to active resolution
    return get_active_form_configuration_service(db, current_user)


def _iter_custom_fields(sections: list[dict]):
    for section in sections:
        for field in section.get("fields") or []:
            if field.get("source") == "custom" and field.get("is_enabled", True):
                yield field


def _iter_enabled_fields(sections: list[dict]):
    for section in sections:
        if not section.get("is_enabled", True):
            continue
        for field in section.get("fields") or []:
            if field.get("is_enabled", True):
                yield field


def _normalize_custom_values_input(custom_values) -> list[dict]:
    """Accept list[{field_id,value}] or Record<field_key|field_id, value>."""
    if custom_values is None:
        return []
    if isinstance(custom_values, dict):
        return [{"field_id": str(k), "value": v} for k, v in custom_values.items()]
    if isinstance(custom_values, list):
        return custom_values
    raise HTTPException(status_code=400, detail="custom_values must be an object or array")


def validate_custom_values(custom_values, sections: list[dict]) -> list[dict]:
    items = _normalize_custom_values_input(custom_values)
    if not items:
        # still enforce required customs
        for field in _iter_custom_fields(sections):
            if field.get("required"):
                raise HTTPException(status_code=400, detail=f"Required custom field missing: {field.get('label')}")
        return []

    by_id = {f["id"]: f for f in _iter_custom_fields(sections)}
    by_key = {f.get("stable_key") or f.get("label"): f for f in _iter_custom_fields(sections)}
    normalized: list[dict] = []
    seen: set[str] = set()

    for item in items:
        if isinstance(item, dict):
            fid = str(item.get("field_id") or item.get("id") or item.get("key") or "")
            value = item.get("value")
        else:
            fid = str(getattr(item, "field_id", ""))
            value = getattr(item, "value", None)
        if not fid:
            raise HTTPException(status_code=400, detail="custom_values entry missing field_id/key")
        field_def = by_id.get(fid) or by_key.get(fid)
        if not field_def:
            # Ignore unknown keys that map to core (handled separately) — only fail for unknown custom
            if fid in REGISTRY_BY_KEY:
                continue
            raise HTTPException(status_code=400, detail=f"Unknown custom field: {fid}")
        real_id = field_def["id"]
        if real_id in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate custom value for field_id {real_id}")
        seen.add(real_id)
        _validate_custom_value(field_def, value)
        normalized.append({"field_id": real_id, "value": value})

    for field in _iter_custom_fields(sections):
        if field.get("required") and field["id"] not in seen:
            raise HTTPException(status_code=400, detail=f"Required custom field missing: {field.get('label')}")
    return normalized


def _validate_custom_value(field_def: dict, value) -> None:
    if value is None:
        if field_def.get("required"):
            raise HTTPException(status_code=400, detail=f"Required custom field '{field_def.get('label')}' is empty")
        return
    vtype = field_def.get("value_type") or "string"
    renderer = field_def.get("renderer")
    if vtype == "string" and not isinstance(value, str):
        if renderer == "checkbox":
            if not isinstance(value, bool):
                raise HTTPException(status_code=400, detail=f"Field '{field_def.get('label')}' expects boolean")
        elif not isinstance(value, (str, int, float)):
            raise HTTPException(status_code=400, detail=f"Field '{field_def.get('label')}' expects string")
    elif vtype == "number" and not isinstance(value, (int, float)):
        raise HTTPException(status_code=400, detail=f"Field '{field_def.get('label')}' expects number")
    elif vtype == "boolean" and not isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"Field '{field_def.get('label')}' expects boolean")
    elif vtype == "string[]" and not isinstance(value, list):
        raise HTTPException(status_code=400, detail=f"Field '{field_def.get('label')}' expects array")
    validation = field_def.get("validation") or {}
    if isinstance(value, str):
        if validation.get("min_length") and len(value) < int(validation["min_length"]):
            raise HTTPException(status_code=400, detail=f"Field '{field_def.get('label')}' too short")
        if validation.get("max_length") and len(value) > int(validation["max_length"]):
            raise HTTPException(status_code=400, detail=f"Field '{field_def.get('label')}' too long")
        if validation.get("pattern") and not re.match(str(validation["pattern"]), value):
            raise HTTPException(status_code=400, detail=f"Field '{field_def.get('label')}' format invalid")
    if renderer in ("select", "multi_select"):
        allowed = {str(o.get("value")) for o in field_def.get("options") or [] if o.get("value") is not None}
        if allowed:
            if renderer == "select" and str(value) not in allowed:
                raise HTTPException(status_code=400, detail=f"Invalid select value for '{field_def.get('label')}'")
            if renderer == "multi_select":
                if not isinstance(value, list) or any(str(v) not in allowed for v in value):
                    raise HTTPException(status_code=400, detail=f"Invalid multi_select value for '{field_def.get('label')}'")


def validate_form_required_core_fields(training_data, sections: list[dict], custom_values_map: dict | None = None) -> None:
    payload = training_data.to_model_data() if hasattr(training_data, "to_model_data") else (
        training_data.model_dump() if hasattr(training_data, "model_dump") else dict(training_data)
    )
    extras = custom_values_map or {}
    for field in _iter_enabled_fields(sections):
        if field.get("source") != "core" or not field.get("required"):
            continue
        key = field.get("core_key")
        if not key:
            continue
        val = payload.get(key)
        if (val is None or val == "" or val == []) and key in extras:
            val = extras[key]
        if val is None or val == "" or val == []:
            raise HTTPException(status_code=400, detail=f"Required field '{field.get('label')}' ({key}) is missing")


def resolve_version_for_create(
    db: Session,
    *,
    version_id: UUID | None,
    tenant_id: UUID,
    enterprise_id: UUID | None,
) -> tuple[TrainingFormConfiguration, TrainingFormConfigurationVersion]:
    if version_id:
        version = db.query(TrainingFormConfigurationVersion).filter(TrainingFormConfigurationVersion.id == version_id).first()
        if not version or version.status != "published":
            raise HTTPException(status_code=400, detail="form_configuration_version_id must reference a published version")
        config = db.query(TrainingFormConfiguration).filter(TrainingFormConfiguration.id == version.configuration_id).first()
        if not config or not config.is_active or config.status != "published":
            raise HTTPException(status_code=400, detail="Form configuration is not active/published")
        if config.scope == "selective":
            assignment = db.query(TrainingFormAssignment).filter(
                TrainingFormAssignment.configuration_id == config.id,
                TrainingFormAssignment.tenant_id == tenant_id,
            ).first()
            if not assignment:
                raise HTTPException(status_code=403, detail="Form configuration not assigned to this tenant")
            if enterprise_id and assignment.enterprise_id and str(assignment.enterprise_id) != str(enterprise_id):
                raise HTTPException(status_code=400, detail="Enterprise does not match form assignment")
        return config, version

    resolved = _active_config_for_tenant(db, tenant_id)
    if not resolved:
        raise HTTPException(status_code=404, detail="No active Training form configuration found")
    return resolved


def apply_form_configuration_to_training_data(db: Session, training_data, current_user: dict) -> dict:
    """Validate + return form_configuration_* columns and normalized custom_values."""
    if current_user.get("role") not in ("admin", "super_admin", "provider"):
        raise HTTPException(status_code=403, detail="Enterprise Admin access required")

    tenant_id = resolve_auth_tenant_id_with_db(db, current_user)
    enterprise_id = getattr(training_data, "enterprise_id", None)

    if enterprise_id:
        ent = db.query(Enterprise).filter(Enterprise.id == enterprise_id, Enterprise.is_deleted.is_(False)).first()
        if not ent:
            raise HTTPException(status_code=404, detail="Enterprise not found")
        if not tenant_id and ent.tenant_id:
            tenant_id = ent.tenant_id
        if tenant_id and ent.tenant_id and str(ent.tenant_id) != str(tenant_id):
            if current_user.get("role") not in ("admin", "super_admin"):
                raise HTTPException(status_code=403, detail="enterprise_id does not match authenticated tenant")
    else:
        payload_tenant = getattr(training_data, "tenant_id", None)
        if not tenant_id and payload_tenant:
            tenant_id = payload_tenant
        if not tenant_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    "tenant_id could not be resolved from the WebAuth session or Training payload "
                    "(send enterprise_id or tenant_id when the session token has no tenant claim)"
                ),
            )
        ent = _resolve_enterprise_for_tenant(db, UUID(str(tenant_id)))
        if not ent:
            raise HTTPException(status_code=404, detail="No enterprise found for authenticated tenant")
        training_data.enterprise_id = ent.id
        enterprise_id = ent.id

    tenant_uuid = UUID(str(tenant_id)) if tenant_id else (ent.tenant_id if ent else None)
    if not tenant_uuid:
        raise HTTPException(status_code=400, detail="tenant_id is required for form configuration resolution")

    if not getattr(training_data, "tenant_id", None):
        training_data.tenant_id = tenant_uuid

    version_id = getattr(training_data, "form_configuration_version_id", None)
    config, version = resolve_version_for_create(
        db,
        version_id=version_id,
        tenant_id=tenant_uuid,
        enterprise_id=enterprise_id,
    )
    sections = normalize_sections(version.sections or [], assign_ids=False)

    raw_custom = getattr(training_data, "custom_values", None)
    custom_map: dict = {}
    if isinstance(raw_custom, dict):
        custom_map = {str(k): v for k, v in raw_custom.items()}

    validate_form_required_core_fields(training_data, sections, custom_map)
    custom_values = validate_custom_values(raw_custom, sections)
    return {
        "form_configuration_id": config.id,
        "form_configuration_version_id": version.id,
        "custom_values": custom_values,
        "tenant_id": tenant_uuid,
        "enterprise_id": enterprise_id,
    }


def apply_form_configuration_to_training_update(db: Session, training, update_data, current_user: dict) -> dict | None:
    if getattr(update_data, "custom_values", None) is None:
        return None
    version_id = training.form_configuration_version_id
    if not version_id:
        return {"custom_values": validate_custom_values(update_data.custom_values, [])}
    version = db.query(TrainingFormConfigurationVersion).filter(TrainingFormConfigurationVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=400, detail="Training linked to missing form configuration version")
    sections = normalize_sections(version.sections or [], assign_ids=False)
    return {"custom_values": validate_custom_values(update_data.custom_values, sections)}
