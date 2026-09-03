"""Event Form Configuration — Super Admin builder and runtime resolution."""

from __future__ import annotations

import re
import uuid
from copy import deepcopy
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.enterprise_model import Enterprise
from app.models.event_form_config_model import (
    EventFormAssignment,
    EventFormAudit,
    EventFormConfiguration,
    EventFormConfigurationVersion,
)
from app.services.event_form_registry import (
    CUSTOM_RENDERERS,
    DOMAIN_REQUIRED_CORE_KEYS,
    LEGACY_CONFIGURATION_ID,
    LEGACY_VERSION_ID,
    NON_REPEATABLE_CORE_KEYS,
    REGISTRY_BY_KEY,
    build_default_sections,
    get_field_registry,
)


def _actor(current_user: dict | None) -> str | None:
    if not current_user:
        return None
    return str(current_user.get("id") or current_user.get("email") or "")


def _audit(
    db: Session,
    *,
    configuration_id: UUID | None,
    version_id: UUID | None,
    action: str,
    actor_id: str | None,
    before: dict | None = None,
    after: dict | None = None,
):
    db.add(
        EventFormAudit(
            configuration_id=configuration_id,
            version_id=version_id,
            actor_id=actor_id,
            action=action,
            before=before,
            after=after,
        )
    )


def _gen_stable(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def normalize_sections(raw_sections: list, *, assign_ids: bool = True) -> list[dict]:
    sections = deepcopy(raw_sections or [])
    normalized: list[dict] = []
    for section in sorted(sections, key=lambda s: int(s.get("position") or 0)):
        if not section.get("is_enabled", True):
            continue
        sec = {
            "id": section.get("id") or (str(uuid.uuid4()) if assign_ids else section.get("id")),
            "stable_key": section.get("stable_key") or _gen_stable("section"),
            "label": section.get("label") or "Section",
            "description": section.get("description"),
            "position": int(section.get("position") or len(normalized) + 1),
            "is_enabled": bool(section.get("is_enabled", True)),
            "fields": [],
        }
        fields = []
        for field in sorted(section.get("fields") or [], key=lambda f: int(f.get("position") or 0)):
            if not field.get("is_enabled", True):
                continue
            source = field.get("source") or "custom"
            core_key = field.get("core_key")
            reg = REGISTRY_BY_KEY.get(core_key) if source == "core" and core_key else None
            renderer = field.get("renderer") or (reg["default_renderer"] if reg else "text")
            value_type = field.get("value_type") or (reg["value_type"] if reg else "string")
            entry = {
                "id": field.get("id") or (str(uuid.uuid4()) if assign_ids else field.get("id")),
                "source": source,
                "core_key": core_key if source == "core" else None,
                "stable_key": field.get("stable_key") or (_gen_stable("custom") if source == "custom" else None),
                "label": field.get("label") or (reg["display_name"] if reg else "Field"),
                "renderer": renderer,
                "value_type": value_type,
                "required": bool(field.get("required", False)),
                "is_enabled": bool(field.get("is_enabled", True)),
                "position": int(field.get("position") or len(fields) + 1),
                "placeholder": field.get("placeholder"),
                "help_text": field.get("help_text"),
                "options": list(field.get("options") or []),
                "validation": dict(field.get("validation") or {}),
            }
            fields.append(entry)
        sec["fields"] = fields
        normalized.append(sec)
    return normalized


def validate_sections_for_publish(sections: list[dict], *, scope: str) -> None:
    errors: list[str] = []
    seen_core: set[str] = set()
    enabled_core: set[str] = set()

    for section in sections:
        if not section.get("is_enabled", True):
            continue
        for field in section.get("fields") or []:
            if not field.get("is_enabled", True):
                continue
            source = field.get("source")
            if source == "core":
                core_key = field.get("core_key")
                if not core_key or core_key not in REGISTRY_BY_KEY:
                    errors.append(f"Unknown core field: {core_key!r}")
                    continue
                reg = REGISTRY_BY_KEY[core_key]
                if core_key in seen_core:
                    errors.append(f"Duplicate core field: {core_key}")
                seen_core.add(core_key)
                enabled_core.add(core_key)
                renderer = field.get("renderer")
                if renderer not in reg["allowed_renderers"]:
                    errors.append(f"Renderer '{renderer}' not allowed for core field '{core_key}'")
                if reg["required_by_domain"]:
                    if not field.get("required", True):
                        errors.append(f"Domain-required field '{core_key}' cannot be optional")
                if reg["required_by_domain"] and not field.get("is_enabled", True):
                    errors.append(f"Domain-required field '{core_key}' cannot be disabled")
            elif source == "custom":
                renderer = field.get("renderer")
                if renderer not in CUSTOM_RENDERERS:
                    errors.append(f"Unsupported custom renderer: {renderer}")
                if not field.get("label"):
                    errors.append("Custom field missing label")
                if renderer in ("select", "multi_select"):
                    opts = field.get("options") or []
                    if not opts:
                        errors.append(f"Select field '{field.get('label')}' requires options")
                    for opt in opts:
                        if not opt.get("value"):
                            errors.append(f"Select option missing value in '{field.get('label')}'")
            else:
                errors.append(f"Invalid field source: {source}")

    for required_key in DOMAIN_REQUIRED_CORE_KEYS:
        if required_key not in enabled_core:
            errors.append(f"Missing required domain field: {required_key}")

    if scope == "selective" and not sections:
        errors.append("Selective configuration must have at least one enabled section")

    if errors:
        raise HTTPException(status_code=400, detail={"message": "Configuration publish validation failed", "errors": errors})


def _version_to_response(version: EventFormConfigurationVersion) -> dict:
    sections = normalize_sections(version.sections or [], assign_ids=False)
    return {
        "id": version.id,
        "configuration_id": version.configuration_id,
        "version": version.version,
        "status": version.status,
        "sections": sections,
        "created_by": version.created_by,
        "created_at": version.created_at,
        "published_at": version.published_at,
    }


def _config_summary(config: EventFormConfiguration) -> dict:
    return {
        "id": config.id,
        "name": config.name,
        "description": config.description,
        "scope": config.scope,
        "status": config.status,
        "is_active": config.is_active,
        "current_version": config.current_version,
        "created_by": config.created_by,
        "created_at": config.created_at,
        "updated_at": config.updated_at,
        "published_at": config.published_at,
    }


def _get_config_or_404(db: Session, config_id: UUID) -> EventFormConfiguration:
    config = db.query(EventFormConfiguration).filter(EventFormConfiguration.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="Event form configuration not found")
    return config


def _get_draft_version(db: Session, config_id: UUID) -> EventFormConfigurationVersion | None:
    return (
        db.query(EventFormConfigurationVersion)
        .filter(
            EventFormConfigurationVersion.configuration_id == config_id,
            EventFormConfigurationVersion.status == "draft",
        )
        .order_by(EventFormConfigurationVersion.version.desc())
        .first()
    )


def _get_published_version(db: Session, config_id: UUID, version_num: int | None = None) -> EventFormConfigurationVersion | None:
    q = db.query(EventFormConfigurationVersion).filter(
        EventFormConfigurationVersion.configuration_id == config_id,
        EventFormConfigurationVersion.status == "published",
    )
    if version_num is not None:
        q = q.filter(EventFormConfigurationVersion.version == version_num)
    return q.order_by(EventFormConfigurationVersion.version.desc()).first()


def list_configurations_service(db: Session) -> list[dict]:
    rows = db.query(EventFormConfiguration).order_by(EventFormConfiguration.created_at.desc()).all()
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
    config = EventFormConfiguration(
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
    version = EventFormConfigurationVersion(
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
        # Editing published → new draft version
        published = _get_published_version(db, config_id)
        if not published:
            raise HTTPException(status_code=400, detail="No published version to fork from")
        new_version_num = config.current_version + 1
        draft = EventFormConfigurationVersion(
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
        draft = EventFormConfigurationVersion(
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
    db.refresh(config)
    db.refresh(draft)
    return get_configuration_service(db, config_id)


def delete_configuration_service(db: Session, config_id: UUID, current_user: dict) -> dict:
    config = _get_config_or_404(db, config_id)
    if str(config.id) == LEGACY_CONFIGURATION_ID:
        raise HTTPException(status_code=400, detail="Legacy default configuration cannot be deleted")
    if config.status == "published":
        raise HTTPException(status_code=400, detail="Published configurations must be retired, not deleted")
    from app.models.event_model import Event
    referenced = db.query(Event).filter(Event.form_configuration_id == config_id).count()
    if referenced:
        raise HTTPException(status_code=400, detail="Configuration referenced by Events — retire instead")
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
            db.query(EventFormConfiguration)
            .filter(
                EventFormConfiguration.scope == "global",
                EventFormConfiguration.is_active.is_(True),
                EventFormConfiguration.id != config_id,
                EventFormConfiguration.status == "published",
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
            db.query(EventFormConfiguration)
            .filter(
                EventFormConfiguration.scope == "global",
                EventFormConfiguration.is_active.is_(True),
                EventFormConfiguration.id != config_id,
            )
            .all()
        )
        for other in others:
            other.is_active = False
            _audit(db, configuration_id=other.id, version_id=None, action="deactivated", actor_id=_actor(current_user), after={"reason": "replaced_by_global_activation"})

    if config.scope == "selective":
        tenant_ids = [a.tenant_id for a in db.query(EventFormAssignment).filter(EventFormAssignment.configuration_id == config_id).all()]
        for tid in tenant_ids:
            conflicts = (
                db.query(EventFormAssignment)
                .join(EventFormConfiguration, EventFormConfiguration.id == EventFormAssignment.configuration_id)
                .filter(
                    EventFormAssignment.tenant_id == tid,
                    EventFormAssignment.configuration_id != config_id,
                    EventFormConfiguration.is_active.is_(True),
                    EventFormConfiguration.scope == "selective",
                )
                .all()
            )
            for conflict in conflicts:
                other_config = db.query(EventFormConfiguration).filter(EventFormConfiguration.id == conflict.configuration_id).first()
                if other_config:
                    other_config.is_active = False
                    _audit(db, configuration_id=other_config.id, version_id=None, action="deactivated", actor_id=_actor(current_user), after={"reason": f"replaced_by_selective_activation_for_tenant_{tid}"})

    config.is_active = True
    _audit(db, configuration_id=config.id, version_id=published.id, action="activated", actor_id=_actor(current_user))
    db.commit()
    return {"id": config.id, "is_active": True, "status": config.status}


def deactivate_configuration_service(db: Session, config_id: UUID, current_user: dict) -> dict:
    config = _get_config_or_404(db, config_id)
    config.is_active = False
    _audit(db, configuration_id=config.id, version_id=None, action="deactivated", actor_id=_actor(current_user))
    db.commit()
    return {"id": config.id, "is_active": False}


def retire_configuration_service(db: Session, config_id: UUID, current_user: dict) -> dict:
    config = _get_config_or_404(db, config_id)
    if str(config.id) == LEGACY_CONFIGURATION_ID:
        raise HTTPException(status_code=400, detail="Legacy default configuration cannot be retired")
    config.status = "retired"
    config.is_active = False
    _audit(db, configuration_id=config.id, version_id=None, action="retired", actor_id=_actor(current_user))
    db.commit()
    return {"id": config.id, "status": "retired", "is_active": False}


def list_versions_service(db: Session, config_id: UUID) -> list[dict]:
    _get_config_or_404(db, config_id)
    rows = (
        db.query(EventFormConfigurationVersion)
        .filter(EventFormConfigurationVersion.configuration_id == config_id)
        .order_by(EventFormConfigurationVersion.version.desc())
        .all()
    )
    return [_version_to_response(v) for v in rows]


def get_version_service(db: Session, config_id: UUID, version_id: UUID) -> dict:
    version = (
        db.query(EventFormConfigurationVersion)
        .filter(
            EventFormConfigurationVersion.configuration_id == config_id,
            EventFormConfigurationVersion.id == version_id,
        )
        .first()
    )
    if not version:
        raise HTTPException(status_code=404, detail="Configuration version not found")
    return _version_to_response(version)


def list_assignments_service(db: Session, config_id: UUID) -> dict:
    _get_config_or_404(db, config_id)
    rows = db.query(EventFormAssignment).filter(EventFormAssignment.configuration_id == config_id).all()
    return {
        "configuration_id": config_id,
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
    config = _get_config_or_404(db, config_id)
    if config.scope != "selective":
        raise HTTPException(status_code=400, detail="Assignments only apply to selective configurations")

    targets: list[tuple[UUID, UUID | None]] = []
    if payload.assignments:
        for item in payload.assignments:
            tid = item.tenant_id
            eid = item.enterprise_id
            if eid:
                _verify_enterprise_tenant(db, eid, tid)
            elif not _resolve_enterprise_for_tenant(db, tid):
                raise HTTPException(status_code=404, detail=f"No enterprise linked to tenant {tid}")
            targets.append((tid, eid or (_resolve_enterprise_for_tenant(db, tid).id if _resolve_enterprise_for_tenant(db, tid) else None)))
    elif payload.tenant_ids:
        for tid in payload.tenant_ids:
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
        raise HTTPException(status_code=400, detail="Provide tenant_ids, enterprise_ids, or assignments")

    # Remove assignments for this config not in new set
    new_tenant_ids = {t[0] for t in targets}
    existing = db.query(EventFormAssignment).filter(EventFormAssignment.configuration_id == config_id).all()
    for row in existing:
        if row.tenant_id not in new_tenant_ids:
            db.delete(row)
            _audit(db, configuration_id=config_id, version_id=None, action="tenant_removed", actor_id=_actor(current_user), before={"tenant_id": str(row.tenant_id)})

    for tid, eid in targets:
        conflict = db.query(EventFormAssignment).filter(EventFormAssignment.tenant_id == tid).first()
        if conflict and conflict.configuration_id != config_id:
            other = db.query(EventFormConfiguration).filter(EventFormConfiguration.id == conflict.configuration_id).first()
            if other and other.is_active:
                raise HTTPException(
                    status_code=409,
                    detail=f"Tenant {tid} already assigned to active configuration '{other.name}'. Deactivate or reassign first.",
                )
            db.delete(conflict)
        existing_same = db.query(EventFormAssignment).filter(EventFormAssignment.configuration_id == config_id, EventFormAssignment.tenant_id == tid).first()
        if existing_same:
            existing_same.enterprise_id = eid
        else:
            db.add(EventFormAssignment(configuration_id=config_id, tenant_id=tid, enterprise_id=eid, created_by=_actor(current_user)))
            _audit(db, configuration_id=config_id, version_id=None, action="tenant_assigned", actor_id=_actor(current_user), after={"tenant_id": str(tid)})

    db.commit()
    return list_assignments_service(db, config_id)


def list_audit_service(db: Session, config_id: UUID) -> list[dict]:
    _get_config_or_404(db, config_id)
    rows = (
        db.query(EventFormAudit)
        .filter(EventFormAudit.configuration_id == config_id)
        .order_by(EventFormAudit.created_at.desc())
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


def resolve_enterprise_context(db: Session, current_user: dict) -> tuple[Enterprise, UUID]:
    """Resolve Enterprise + tenant from authenticated user — do not trust client tenant_id."""
    if current_user.get("role") not in ("admin", "super_admin", "provider"):
        raise HTTPException(status_code=403, detail="Enterprise Admin access required")
    tenant_id = current_user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="Authenticated tenant_id is required")
    tenant_uuid = UUID(str(tenant_id))
    enterprise = _resolve_enterprise_for_tenant(db, tenant_uuid)
    if not enterprise:
        raise HTTPException(status_code=404, detail="No enterprise found for authenticated tenant")
    if enterprise.status in ("draft", "pending", "inactive"):
        raise HTTPException(status_code=400, detail=f"Enterprise not approved (status={enterprise.status})")
    return enterprise, tenant_uuid


def _active_config_for_tenant(db: Session, tenant_id: UUID) -> tuple[EventFormConfiguration, EventFormConfigurationVersion] | None:
    assignment = db.query(EventFormAssignment).filter(EventFormAssignment.tenant_id == tenant_id).first()
    if assignment:
        config = db.query(EventFormConfiguration).filter(
            EventFormConfiguration.id == assignment.configuration_id,
            EventFormConfiguration.is_active.is_(True),
            EventFormConfiguration.status == "published",
            EventFormConfiguration.scope == "selective",
        ).first()
        if config:
            version = _get_published_version(db, config.id)
            if version:
                return config, version

    global_config = (
        db.query(EventFormConfiguration)
        .filter(
            EventFormConfiguration.scope == "global",
            EventFormConfiguration.is_active.is_(True),
            EventFormConfiguration.status == "published",
        )
        .order_by(EventFormConfiguration.published_at.desc().nullslast())
        .first()
    )
    if global_config:
        version = _get_published_version(db, global_config.id)
        if version:
            return global_config, version
    return None


def _legacy_config_version(db: Session) -> tuple[EventFormConfiguration, EventFormConfigurationVersion]:
    config = db.query(EventFormConfiguration).filter(EventFormConfiguration.id == UUID(LEGACY_CONFIGURATION_ID)).first()
    version = db.query(EventFormConfigurationVersion).filter(EventFormConfigurationVersion.id == UUID(LEGACY_VERSION_ID)).first()
    if config and version:
        return config, version
    raise HTTPException(status_code=503, detail="Legacy default Event form configuration is not seeded")


def build_active_response(config: EventFormConfiguration, version: EventFormConfigurationVersion) -> dict:
    sections = normalize_sections(version.sections or [], assign_ids=False)
    return {
        "configuration_id": config.id,
        "version_id": version.id,
        "name": config.name,
        "scope": config.scope,
        "version": version.version,
        "sections": sections,
        "configuration_version": f"{config.id}:{version.version}",
    }


def get_active_form_configuration_service(db: Session, current_user: dict) -> dict:
    _, tenant_id = resolve_enterprise_context(db, current_user)
    resolved = _active_config_for_tenant(db, tenant_id)
    if not resolved:
        resolved = _legacy_config_version(db)
    config, version = resolved
    return build_active_response(config, version)


def get_event_form_configuration_service(db: Session, event_id: UUID, current_user: dict) -> dict:
    from app.repository.event_repo import get_event_by_id

    event = get_event_by_id(db, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.form_configuration_version_id:
        version = db.query(EventFormConfigurationVersion).filter(EventFormConfigurationVersion.id == event.form_configuration_version_id).first()
        if version:
            config = db.query(EventFormConfiguration).filter(EventFormConfiguration.id == version.configuration_id).first()
            if config:
                return build_active_response(config, version)
    # Legacy fallback
    config, version = _legacy_config_version(db)
    return build_active_response(config, version)


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


def validate_custom_values(custom_values: list | None, sections: list[dict]) -> list[dict]:
    if not custom_values:
        return []
    field_map = {f["id"]: f for f in _iter_custom_fields(sections)}
    normalized: list[dict] = []
    seen: set[str] = set()
    for item in custom_values:
        if isinstance(item, dict):
            fid = str(item.get("field_id") or item.get("id") or "")
            value = item.get("value")
        else:
            fid = str(getattr(item, "field_id", ""))
            value = getattr(item, "value", None)
        if not fid:
            raise HTTPException(status_code=400, detail="custom_values entry missing field_id")
        if fid in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate custom value for field_id {fid}")
        seen.add(fid)
        field_def = field_map.get(fid)
        if not field_def:
            raise HTTPException(status_code=400, detail=f"Unknown custom field_id: {fid}")
        _validate_custom_value(field_def, value)
        normalized.append({"field_id": fid, "value": value})
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
        if renderer == "select" and str(value) not in allowed:
            raise HTTPException(status_code=400, detail=f"Invalid select value for '{field_def.get('label')}'")
        if renderer == "multi_select":
            if not isinstance(value, list) or any(str(v) not in allowed for v in value):
                raise HTTPException(status_code=400, detail=f"Invalid multi_select value for '{field_def.get('label')}'")


def validate_form_required_core_fields(event_data, sections: list[dict]) -> None:
    """Supplement domain validation with configuration-required enabled fields."""
    payload = event_data.to_model_data() if hasattr(event_data, "to_model_data") else event_data.model_dump()
    for field in _iter_enabled_fields(sections):
        if field.get("source") != "core" or not field.get("required"):
            continue
        key = field.get("core_key")
        if not key:
            continue
        val = payload.get(key)
        if val is None or val == "" or val == []:
            raise HTTPException(status_code=400, detail=f"Required field '{field.get('label')}' ({key}) is missing")


def resolve_version_for_create(
    db: Session,
    *,
    version_id: UUID | None,
    tenant_id: UUID,
    enterprise_id: UUID | None,
) -> tuple[EventFormConfiguration, EventFormConfigurationVersion]:
    if version_id:
        version = db.query(EventFormConfigurationVersion).filter(EventFormConfigurationVersion.id == version_id).first()
        if not version or version.status != "published":
            raise HTTPException(status_code=400, detail="form_configuration_version_id must reference a published version")
        config = db.query(EventFormConfiguration).filter(EventFormConfiguration.id == version.configuration_id).first()
        if not config or not config.is_active or config.status != "published":
            raise HTTPException(status_code=400, detail="Form configuration is not active/published")
        if config.scope == "selective":
            assignment = db.query(EventFormAssignment).filter(
                EventFormAssignment.configuration_id == config.id,
                EventFormAssignment.tenant_id == tenant_id,
            ).first()
            if not assignment:
                raise HTTPException(status_code=403, detail="Form configuration not assigned to this tenant")
            if enterprise_id and assignment.enterprise_id and str(assignment.enterprise_id) != str(enterprise_id):
                raise HTTPException(status_code=400, detail="Enterprise does not match form assignment")
        return config, version

    resolved = _active_config_for_tenant(db, tenant_id)
    if not resolved:
        return _legacy_config_version(db)
    return resolved


def apply_form_configuration_to_event_data(db: Session, event_data, current_user: dict) -> dict:
    """Returns extra Event columns: form_configuration_id, form_configuration_version_id, custom_values."""
    enterprise, tenant_id = resolve_enterprise_context(db, current_user)
    if event_data.enterprise_id and str(event_data.enterprise_id) != str(enterprise.id):
        if current_user.get("role") not in ("admin", "super_admin"):
            raise HTTPException(status_code=403, detail="enterprise_id does not match authenticated enterprise")
        _verify_enterprise_tenant(db, event_data.enterprise_id, tenant_id)
    elif not event_data.enterprise_id:
        event_data.enterprise_id = enterprise.id
    if not event_data.tenant_id:
        event_data.tenant_id = tenant_id

    version_id = getattr(event_data, "form_configuration_version_id", None)
    config, version = resolve_version_for_create(
        db,
        version_id=version_id,
        tenant_id=tenant_id,
        enterprise_id=event_data.enterprise_id,
    )
    sections = normalize_sections(version.sections or [], assign_ids=False)
    validate_form_required_core_fields(event_data, sections)
    custom_values = validate_custom_values(getattr(event_data, "custom_values", None), sections)
    return {
        "form_configuration_id": config.id,
        "form_configuration_version_id": version.id,
        "custom_values": custom_values,
        "tenant_id": tenant_id,
        "enterprise_id": event_data.enterprise_id,
    }


def apply_form_configuration_to_event_update(db: Session, event, update_data, current_user: dict) -> dict | None:
    if getattr(update_data, "custom_values", None) is None:
        return None
    version_id = event.form_configuration_version_id
    if not version_id:
        _, version = _legacy_config_version(db)
    else:
        version = db.query(EventFormConfigurationVersion).filter(EventFormConfigurationVersion.id == version_id).first()
        if not version:
            raise HTTPException(status_code=400, detail="Event linked to missing form configuration version")
    sections = normalize_sections(version.sections or [], assign_ids=False)
    return {"custom_values": validate_custom_values(update_data.custom_values, sections)}
