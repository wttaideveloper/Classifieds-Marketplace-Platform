"""Event Form Configuration — Super Admin builder and runtime resolution."""

from __future__ import annotations

import logging
import re
import uuid
from copy import deepcopy
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.auth_context import resolve_auth_tenant_id_with_db
from app.models.enterprise_model import Enterprise

logger = logging.getLogger(__name__)
from app.models.event_form_config_model import (
    EventFormAssignment,
    EventFormAudit,
    EventFormConfiguration,
    EventFormConfigurationVersion,
)
from app.services.event_form_registry import (
    COMPOSITE_CORE_KEYS,
    CUSTOM_RENDERERS,
    DOMAIN_REQUIRED_CORE_KEYS,
    LEGACY_CONFIGURATION_ID,
    LEGACY_VERSION_ID,
    NON_REPEATABLE_CORE_KEYS,
    REGISTRY_BY_KEY,
    build_default_sections,
    get_allowed_composite_subfields,
    get_field_registry,
    normalize_composite_config,
)
from app.services.invigorate_auth_client import resolve_tenant_ids_from_slugs


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
            if source == "core" and core_key in COMPOSITE_CORE_KEYS:
                entry["composite_config"] = normalize_composite_config(core_key, field.get("composite_config"))
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
                if core_key in COMPOSITE_CORE_KEYS:
                    composite = field.get("composite_config") or normalize_composite_config(core_key, None)
                    allowed = get_allowed_composite_subfields(core_key)
                    enabled_sub = composite.get("enabled_fields") or []
                    required_sub = composite.get("required_fields") or []
                    if field.get("is_enabled", True) and not enabled_sub:
                        errors.append(f"Composite field '{core_key}' must have at least one enabled sub-field")
                    unknown_enabled = [f for f in enabled_sub if f not in allowed]
                    if unknown_enabled:
                        errors.append(f"Unknown sub-fields for '{core_key}': {', '.join(unknown_enabled)}")
                    unknown_required = [f for f in required_sub if f not in allowed]
                    if unknown_required:
                        errors.append(f"Unknown required sub-fields for '{core_key}': {', '.join(unknown_required)}")
                    not_enabled_required = [f for f in required_sub if f not in enabled_sub]
                    if not_enabled_required:
                        errors.append(
                            f"Required sub-fields must be enabled for '{core_key}': {', '.join(not_enabled_required)}"
                        )
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
    return {"id": config.id, "is_active": False, "status": config.status}


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
        raise HTTPException(status_code=400, detail="Provide tenant_ids, tenant_slugs, enterprise_ids, or assignments")

    # Remove assignments for this config not in new set
    new_tenant_ids = {t[0] for t in targets}
    existing = db.query(EventFormAssignment).filter(EventFormAssignment.configuration_id == config_id).all()
    for row in existing:
        if row.tenant_id not in new_tenant_ids:
            db.delete(row)
            _audit(db, configuration_id=config_id, version_id=None, action="tenant_removed", actor_id=_actor(current_user), before={"tenant_id": str(row.tenant_id)})
    db.flush()

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
            db.flush()
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


def resolve_enterprise_context(
    db: Session,
    current_user: dict,
    *,
    payload_enterprise_id: UUID | None = None,
    payload_tenant_id: UUID | None = None,
) -> tuple[Enterprise, UUID]:
    """Resolve Enterprise + tenant for Event create.

    Order (WebAuth cookie / Bearer — never query-param tenant_id):
    1. Session/JWT tenant claims (+ enterprise_id / tenant_slug fallbacks)
    2. Event body ``enterprise_id`` → ``Enterprise.tenant_id``
    3. Event body ``tenant_id`` (same as templates / Enterprise create when session lacks claim)
    """
    if current_user.get("role") not in ("admin", "super_admin", "provider"):
        raise HTTPException(status_code=403, detail="Enterprise Admin access required")

    auth_tenant_id = resolve_auth_tenant_id_with_db(db, current_user)
    tenant_id = auth_tenant_id
    enterprise: Enterprise | None = None

    if payload_enterprise_id:
        enterprise = (
            db.query(Enterprise)
            .filter(Enterprise.id == payload_enterprise_id, Enterprise.is_deleted.is_(False))
            .first()
        )
        if not enterprise:
            raise HTTPException(status_code=404, detail="Enterprise not found")
        if not tenant_id and enterprise.tenant_id:
            tenant_id = str(enterprise.tenant_id)
        if (
            auth_tenant_id
            and enterprise.tenant_id
            and str(enterprise.tenant_id) != str(auth_tenant_id)
            and current_user.get("role") not in ("admin", "super_admin")
        ):
            raise HTTPException(status_code=403, detail="enterprise_id does not match authenticated tenant")

    if not tenant_id and payload_tenant_id:
        # Authenticated Enterprise Admin WebAuth often has no tenant claim; body tenant_id
        # comes from identity (/tenant/me) while the session cookie proves the user.
        tenant_id = str(payload_tenant_id)

    if auth_tenant_id and payload_tenant_id and str(auth_tenant_id) != str(payload_tenant_id):
        if current_user.get("role") not in ("admin", "super_admin"):
            raise HTTPException(status_code=403, detail="Supplied tenant_id does not belong to authenticated user")

    if not tenant_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "tenant_id could not be resolved from the WebAuth session or Event payload "
                "(send enterprise_id or tenant_id on the Event body when the session token has no tenant claim)"
            ),
        )

    tenant_uuid = UUID(str(tenant_id))
    if enterprise is None:
        enterprise = _resolve_enterprise_for_tenant(db, tenant_uuid)
    if not enterprise:
        raise HTTPException(status_code=404, detail="No enterprise found for authenticated tenant")
    if enterprise.status in ("draft", "pending", "inactive"):
        raise HTTPException(status_code=400, detail=f"Enterprise not approved (status={enterprise.status})")
    return enterprise, tenant_uuid


def _resolve_active_form_configuration(
    db: Session,
    tenant_id: UUID | None,
) -> tuple[EventFormConfiguration, EventFormConfigurationVersion]:
    """active+published selective assigned to tenant → active+published global
    → active+published legacy → 404. The seeded legacy config is a fallback
    candidate like any other, not an unconditional last resort — if a Super
    Admin has deliberately deactivated it, it must not be served."""
    if tenant_id is not None:
        resolved = _active_config_for_tenant(db, tenant_id)
        if resolved:
            return resolved
    else:
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

    legacy = _active_legacy_config_version(db)
    if legacy:
        return legacy
    raise HTTPException(status_code=404, detail="No active Event form configuration found")


def _active_config_for_tenant(db: Session, tenant_id: UUID) -> tuple[EventFormConfiguration, EventFormConfigurationVersion] | None:
    assignment = db.query(EventFormAssignment).filter(EventFormAssignment.tenant_id == tenant_id).first()
    if assignment:
        raw_config = db.query(EventFormConfiguration).filter(
            EventFormConfiguration.id == assignment.configuration_id,
        ).first()
        config = db.query(EventFormConfiguration).filter(
            EventFormConfiguration.id == assignment.configuration_id,
            EventFormConfiguration.is_active.is_(True),
            EventFormConfiguration.status == "published",
            EventFormConfiguration.scope == "selective",
        ).first()
        logger.info(
            "Event selective assignment lookup: tenant_id=%s -> configuration_id=%s "
            "(is_active=%s status=%s scope=%s) matched=%s",
            tenant_id, assignment.configuration_id,
            getattr(raw_config, "is_active", None), getattr(raw_config, "status", None), getattr(raw_config, "scope", None),
            config is not None,
        )
        if config:
            version = _get_published_version(db, config.id)
            if version:
                return config, version
    else:
        logger.info("Event selective assignment lookup: no EventFormAssignment row for tenant_id=%s", tenant_id)

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


def _active_legacy_config_version(db: Session) -> tuple[EventFormConfiguration, EventFormConfigurationVersion] | None:
    """The seeded legacy config, but only when it's actually active+published
    — used by the active-resolution path, where a deactivated legacy config
    must not be silently served as if it were still the default."""
    config = db.query(EventFormConfiguration).filter(
        EventFormConfiguration.id == UUID(LEGACY_CONFIGURATION_ID),
        EventFormConfiguration.is_active.is_(True),
        EventFormConfiguration.status == "published",
    ).first()
    if not config:
        return None
    version = _get_published_version(db, config.id)
    if not version:
        return None
    return config, version


def _legacy_config_version(db: Session) -> tuple[EventFormConfiguration, EventFormConfigurationVersion]:
    """Unconditional legacy lookup — used only for reconstructing the
    historical form an existing Event/draft was created against, where the
    legacy config's current is_active state is irrelevant."""
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


def _probe_auth_me(access_token: str | None) -> dict:
    """Repeats the exact /auth/me fallback call resolve_auth_tenant_id_with_db
    makes, but reports the outcome instead of swallowing it on failure — the
    fallback's own exception handling makes it invisible without server logs,
    which is exactly the blocker we need to route around."""
    from app.core.auth_context import _unwrap_auth_me_profile, resolve_auth_tenant_id
    from app.core.config import settings

    if not access_token:
        return {"attempted": False, "reason": "no access_token available"}
    base = settings.INVIGORATE_AUTH_BASE_URL.strip()
    if not base:
        return {"attempted": False, "reason": "INVIGORATE_AUTH_BASE_URL not configured"}

    import requests

    url = f"{base.rstrip('/')}/api/v1/auth/me"
    try:
        response = requests.get(url, headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
    except Exception as exc:
        return {"attempted": True, "url": url, "error": f"{type(exc).__name__}: {exc}"}

    result: dict = {"attempted": True, "url": url, "status_code": response.status_code}
    if response.status_code == 404:
        result["outcome"] = "404 — fetch_auth_me_profile treats this as 'no profile', returns None"
        return result
    try:
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["raw_body_snippet"] = response.text[:300]
        return result

    result["response_keys"] = list(payload.keys()) if isinstance(payload, dict) else f"non-dict: {type(payload).__name__}"
    unwrapped = _unwrap_auth_me_profile(payload) if isinstance(payload, dict) else None
    result["unwrapped_keys"] = list(unwrapped.keys()) if unwrapped else None
    result["tenant_id_extracted_from_top_level"] = resolve_auth_tenant_id(payload) if isinstance(payload, dict) else None
    result["tenant_id_extracted_from_unwrapped"] = resolve_auth_tenant_id(unwrapped) if unwrapped else None
    return result


def _probe_tenant_me(access_token: str | None) -> dict:
    """Repeats the exact /tenant/me fallback call resolve_auth_tenant_id_with_db
    now makes (the primary tenant-resolution fallback as of the fix for the
    2026-09-10 case where /auth/me's response had no usable tenant_id field),
    reporting the outcome instead of swallowing it on failure."""
    from app.core.config import settings

    if not access_token:
        return {"attempted": False, "reason": "no access_token available"}
    base = settings.INVIGORATE_AUTH_BASE_URL.strip()
    if not base:
        return {"attempted": False, "reason": "INVIGORATE_AUTH_BASE_URL not configured"}

    import requests

    url = f"{base.rstrip('/')}/api/v1/tenant/me"
    try:
        response = requests.get(url, headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
    except Exception as exc:
        return {"attempted": True, "url": url, "error": f"{type(exc).__name__}: {exc}"}

    result: dict = {"attempted": True, "url": url, "status_code": response.status_code}
    if response.status_code == 404:
        result["outcome"] = "404 — fetch_tenant_me_profile treats this as 'no tenant', returns None"
        return result
    try:
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["raw_body_snippet"] = response.text[:300]
        return result

    data = payload.get("data") if isinstance(payload, dict) else None
    result["response_keys"] = list(payload.keys()) if isinstance(payload, dict) else f"non-dict: {type(payload).__name__}"
    result["data_keys"] = list(data.keys()) if isinstance(data, dict) else None
    result["tenant_id_extracted"] = str(data["id"]) if isinstance(data, dict) and data.get("id") else None
    return result


def _diagnose_active_resolution_failure(
    db: Session,
    tenant_uuid: UUID | None,
    current_user: dict,
    *,
    local_tenant_claim: str | None,
    local_enterprise_claim: str | None,
    access_token_present: bool,
    access_token: str | None = None,
) -> dict:
    """Build a JSON-visible explanation of why no active config resolved —
    lets the caller self-diagnose from the API response body when they have
    no access to server logs, instead of us needing to pull runtime logs."""
    assignment = None
    assignment_config = None
    if tenant_uuid is not None:
        assignment = db.query(EventFormAssignment).filter(EventFormAssignment.tenant_id == tenant_uuid).first()
        if assignment:
            assignment_config = db.query(EventFormConfiguration).filter(
                EventFormConfiguration.id == assignment.configuration_id,
            ).first()

    global_config = (
        db.query(EventFormConfiguration)
        .filter(EventFormConfiguration.scope == "global", EventFormConfiguration.is_active.is_(True), EventFormConfiguration.status == "published")
        .order_by(EventFormConfiguration.published_at.desc().nullslast())
        .first()
    )
    legacy_config = db.query(EventFormConfiguration).filter(EventFormConfiguration.id == UUID(LEGACY_CONFIGURATION_ID)).first()

    return {
        "resolved_tenant_id": str(tenant_uuid) if tenant_uuid else None,
        "local_tenant_claim": local_tenant_claim,
        "local_enterprise_claim": local_enterprise_claim,
        "access_token_present": access_token_present,
        "assignment_found_for_tenant": assignment is not None,
        "assignment_configuration_id": str(assignment.configuration_id) if assignment else None,
        "assignment_configuration_state": (
            {
                "is_active": assignment_config.is_active,
                "status": assignment_config.status,
                "scope": assignment_config.scope,
            }
            if assignment_config is not None
            else None
        ),
        "active_published_global_config_id": str(global_config.id) if global_config else None,
        "legacy_config_state": (
            {"is_active": legacy_config.is_active, "status": legacy_config.status}
            if legacy_config is not None
            else None
        ),
        "tenant_me_fallback_probe": (
            _probe_tenant_me(access_token)
            if tenant_uuid is None and not local_tenant_claim and not local_enterprise_claim
            else "skipped — local claims or resolved_tenant_id already present"
        ),
        "auth_me_fallback_probe": (
            _probe_auth_me(access_token)
            if tenant_uuid is None and not local_tenant_claim and not local_enterprise_claim
            else "skipped — local claims or resolved_tenant_id already present"
        ),
    }


def get_active_form_configuration_service(
    db: Session,
    current_user: dict,
    *,
    access_token: str | None = None,
) -> dict:
    if current_user.get("role") not in ("admin", "super_admin", "provider"):
        raise HTTPException(status_code=403, detail="Enterprise Admin access required")

    from app.core.auth_context import resolve_auth_enterprise_id, resolve_auth_tenant_id

    local_tenant_claim = resolve_auth_tenant_id(current_user)
    local_enterprise_claim = resolve_auth_enterprise_id(current_user)
    tenant_raw = resolve_auth_tenant_id_with_db(db, current_user, access_token=access_token)
    tenant_uuid = UUID(str(tenant_raw)) if tenant_raw else None
    logger.info(
        "Event active-form resolve: user_id=%s local_tenant_claim=%s local_enterprise_claim=%s "
        "resolved_tenant_id=%s access_token_present=%s",
        current_user.get("id"),
        local_tenant_claim,
        local_enterprise_claim,
        tenant_uuid,
        bool(access_token),
    )
    try:
        config, version = _resolve_active_form_configuration(db, tenant_uuid)
    except HTTPException as exc:
        if exc.status_code == 404:
            exc.detail = {
                "message": "No active Event form configuration found",
                "diagnostics": _diagnose_active_resolution_failure(
                    db,
                    tenant_uuid,
                    current_user,
                    local_tenant_claim=local_tenant_claim,
                    local_enterprise_claim=local_enterprise_claim,
                    access_token_present=bool(access_token),
                    access_token=access_token,
                ),
            }
        raise
    logger.info(
        "Event active-form resolved: tenant_id=%s -> configuration_id=%s scope=%s",
        tenant_uuid, config.id, config.scope,
    )
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
    if resolved:
        return resolved

    legacy = _active_legacy_config_version(db)
    if legacy:
        return legacy

    raise HTTPException(
        status_code=400,
        detail=(
            "No active Event form configuration available for this tenant "
            "(no active selective assignment, no active global configuration, "
            "and the Legacy/Default form is inactive) — cannot create Event."
        ),
    )


def apply_form_configuration_to_event_data(db: Session, event_data, current_user: dict) -> dict:
    """Returns extra Event columns: form_configuration_id, form_configuration_version_id, custom_values."""
    enterprise, tenant_id = resolve_enterprise_context(
        db,
        current_user,
        payload_enterprise_id=getattr(event_data, "enterprise_id", None),
        payload_tenant_id=getattr(event_data, "tenant_id", None),
    )
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
