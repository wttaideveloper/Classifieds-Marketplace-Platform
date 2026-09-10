"""Program Form Configuration — Super Admin builder and runtime resolution."""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.auth_context import resolve_auth_tenant_id_with_db
from app.models.enterprise_model import Enterprise

logger = logging.getLogger(__name__)
from app.models.program_form_config_model import (
    ProgramFormAssignment,
    ProgramFormAudit,
    ProgramFormConfiguration,
    ProgramFormConfigurationVersion,
)
from app.services.invigorate_auth_client import resolve_tenant_ids_from_slugs
from app.services.program_form_registry import (
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
        ProgramFormAudit(
            configuration_id=configuration_id,
            version_id=version_id,
            actor_id=actor_id,
            action=action,
            before=before,
            after=after,
        )
    )


def _version_to_response(version: ProgramFormConfigurationVersion) -> dict:
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


def _config_summary(config: ProgramFormConfiguration) -> dict:
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


def _get_config_or_404(db: Session, config_id: UUID) -> ProgramFormConfiguration:
    config = db.query(ProgramFormConfiguration).filter(ProgramFormConfiguration.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="Program form configuration not found")
    return config


def _get_draft_version(db: Session, config_id: UUID) -> ProgramFormConfigurationVersion | None:
    return (
        db.query(ProgramFormConfigurationVersion)
        .filter(
            ProgramFormConfigurationVersion.configuration_id == config_id,
            ProgramFormConfigurationVersion.status == "draft",
        )
        .order_by(ProgramFormConfigurationVersion.version.desc())
        .first()
    )


def _get_published_version(db: Session, config_id: UUID, version_num: int | None = None) -> ProgramFormConfigurationVersion | None:
    q = db.query(ProgramFormConfigurationVersion).filter(
        ProgramFormConfigurationVersion.configuration_id == config_id,
        ProgramFormConfigurationVersion.status == "published",
    )
    if version_num is not None:
        q = q.filter(ProgramFormConfigurationVersion.version == version_num)
    return q.order_by(ProgramFormConfigurationVersion.version.desc()).first()


def list_configurations_service(
    db: Session,
    *,
    status: str | None = None,
    search: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> list[dict]:
    q = db.query(ProgramFormConfiguration)
    if status:
        if status == "active":
            q = q.filter(ProgramFormConfiguration.is_active.is_(True))
        elif status == "archived":
            q = q.filter(ProgramFormConfiguration.status == "retired")
        else:
            q = q.filter(ProgramFormConfiguration.status == status)
    if search:
        term = f"%{search.strip()}%"
        q = q.filter(ProgramFormConfiguration.name.ilike(term))
    q = q.order_by(ProgramFormConfiguration.created_at.desc())
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
    config = ProgramFormConfiguration(
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
    version = ProgramFormConfigurationVersion(
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
        draft = ProgramFormConfigurationVersion(
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
        draft = ProgramFormConfigurationVersion(
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
        raise HTTPException(status_code=400, detail="Default Program form configuration cannot be deleted")
    if config.status == "published":
        raise HTTPException(status_code=400, detail="Published configurations must be retired, not deleted")
    from app.models.program_model import Program

    referenced = db.query(Program).filter(Program.form_configuration_id == config_id).count()
    if referenced:
        raise HTTPException(status_code=400, detail="Configuration referenced by Programs — retire instead")
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
            db.query(ProgramFormConfiguration)
            .filter(
                ProgramFormConfiguration.scope == "global",
                ProgramFormConfiguration.is_active.is_(True),
                ProgramFormConfiguration.id != config_id,
                ProgramFormConfiguration.status == "published",
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
            db.query(ProgramFormConfiguration)
            .filter(
                ProgramFormConfiguration.scope == "global",
                ProgramFormConfiguration.is_active.is_(True),
                ProgramFormConfiguration.id != config_id,
            )
            .all()
        )
        for other in others:
            other.is_active = False
            _audit(db, configuration_id=other.id, version_id=None, action="deactivated", actor_id=_actor(current_user), after={"reason": "replaced_by_global_activation"})

    if config.scope == "selective":
        tenant_ids = [a.tenant_id for a in db.query(ProgramFormAssignment).filter(ProgramFormAssignment.configuration_id == config_id).all()]
        for tid in tenant_ids:
            conflicts = (
                db.query(ProgramFormAssignment)
                .join(ProgramFormConfiguration, ProgramFormConfiguration.id == ProgramFormAssignment.configuration_id)
                .filter(
                    ProgramFormAssignment.tenant_id == tid,
                    ProgramFormAssignment.configuration_id != config_id,
                    ProgramFormConfiguration.is_active.is_(True),
                    ProgramFormConfiguration.scope == "selective",
                )
                .all()
            )
            for conflict in conflicts:
                other_config = db.query(ProgramFormConfiguration).filter(ProgramFormConfiguration.id == conflict.configuration_id).first()
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
        raise HTTPException(status_code=400, detail="Default Program form configuration cannot be retired")
    config.status = "retired"
    config.is_active = False
    _audit(db, configuration_id=config.id, version_id=None, action="retired", actor_id=_actor(current_user))
    db.commit()
    return {"id": config.id, "status": "retired", "is_active": False}


def list_versions_service(db: Session, config_id: UUID) -> list[dict]:
    _get_config_or_404(db, config_id)
    rows = (
        db.query(ProgramFormConfigurationVersion)
        .filter(ProgramFormConfigurationVersion.configuration_id == config_id)
        .order_by(ProgramFormConfigurationVersion.version.desc())
        .all()
    )
    return [_version_to_response(r) for r in rows]


def get_version_service(db: Session, config_id: UUID, version_id: UUID) -> dict:
    _get_config_or_404(db, config_id)
    version = (
        db.query(ProgramFormConfigurationVersion)
        .filter(
            ProgramFormConfigurationVersion.configuration_id == config_id,
            ProgramFormConfigurationVersion.id == version_id,
        )
        .first()
    )
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    return _version_to_response(version)


def list_assignments_service(db: Session, config_id: UUID) -> dict:
    config = _get_config_or_404(db, config_id)
    rows = db.query(ProgramFormAssignment).filter(ProgramFormAssignment.configuration_id == config_id).all()
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

    Frontend contract: `{enterprise_ids: []}` or `{is_global: true}` → make config
    global (clear assignments). Non-empty enterprise_ids/tenant_ids/tenant_slugs/
    assignments → selective assignments.
    """
    config = _get_config_or_404(db, config_id)

    make_global = payload.is_global is True or (payload.enterprise_ids is not None and len(payload.enterprise_ids) == 0)
    if make_global:
        existing = db.query(ProgramFormAssignment).filter(ProgramFormAssignment.configuration_id == config_id).all()
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
    existing = db.query(ProgramFormAssignment).filter(ProgramFormAssignment.configuration_id == config_id).all()
    for row in existing:
        if row.tenant_id not in new_tenant_ids:
            db.delete(row)
            _audit(db, configuration_id=config_id, version_id=None, action="tenant_removed", actor_id=_actor(current_user), before={"tenant_id": str(row.tenant_id)})
    db.flush()

    for tid, eid in targets:
        conflict = db.query(ProgramFormAssignment).filter(ProgramFormAssignment.tenant_id == tid).first()
        if conflict and conflict.configuration_id != config_id:
            other = db.query(ProgramFormConfiguration).filter(ProgramFormConfiguration.id == conflict.configuration_id).first()
            if other and other.is_active:
                raise HTTPException(
                    status_code=409,
                    detail=f"Tenant {tid} already assigned to active configuration '{other.name}'. Deactivate or reassign first.",
                )
            db.delete(conflict)
            db.flush()
        existing_same = (
            db.query(ProgramFormAssignment)
            .filter(ProgramFormAssignment.configuration_id == config_id, ProgramFormAssignment.tenant_id == tid)
            .first()
        )
        if existing_same:
            existing_same.enterprise_id = eid
        else:
            db.add(ProgramFormAssignment(configuration_id=config_id, tenant_id=tid, enterprise_id=eid, created_by=_actor(current_user)))
            _audit(db, configuration_id=config_id, version_id=None, action="tenant_assigned", actor_id=_actor(current_user), after={"tenant_id": str(tid)})

    db.commit()
    return list_assignments_service(db, config_id)


def list_audit_service(db: Session, config_id: UUID) -> list[dict]:
    _get_config_or_404(db, config_id)
    rows = (
        db.query(ProgramFormAudit)
        .filter(ProgramFormAudit.configuration_id == config_id)
        .order_by(ProgramFormAudit.created_at.desc())
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


def _active_config_for_tenant(db: Session, tenant_id: UUID) -> tuple[ProgramFormConfiguration, ProgramFormConfigurationVersion] | None:
    assignment = db.query(ProgramFormAssignment).filter(ProgramFormAssignment.tenant_id == tenant_id).first()
    if assignment:
        raw_config = db.query(ProgramFormConfiguration).filter(
            ProgramFormConfiguration.id == assignment.configuration_id,
        ).first()
        config = db.query(ProgramFormConfiguration).filter(
            ProgramFormConfiguration.id == assignment.configuration_id,
            ProgramFormConfiguration.is_active.is_(True),
            ProgramFormConfiguration.status == "published",
            ProgramFormConfiguration.scope == "selective",
        ).first()
        logger.info(
            "Program selective assignment lookup: tenant_id=%s -> configuration_id=%s "
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
        logger.info("Program selective assignment lookup: no ProgramFormAssignment row for tenant_id=%s", tenant_id)

    global_config = (
        db.query(ProgramFormConfiguration)
        .filter(
            ProgramFormConfiguration.scope == "global",
            ProgramFormConfiguration.is_active.is_(True),
            ProgramFormConfiguration.status == "published",
        )
        .order_by(ProgramFormConfiguration.published_at.desc().nullslast())
        .first()
    )
    if global_config:
        version = _get_published_version(db, global_config.id)
        if version:
            return global_config, version
    return None


def build_active_response(config: ProgramFormConfiguration, version: ProgramFormConfigurationVersion) -> dict:
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

    from app.core.auth_context import resolve_auth_enterprise_id, resolve_auth_tenant_id

    tenant_raw = resolve_auth_tenant_id_with_db(db, current_user, access_token=access_token)
    tenant_uuid = UUID(str(tenant_raw)) if tenant_raw else None
    logger.info(
        "Program active-form resolve: user_id=%s local_tenant_claim=%s local_enterprise_claim=%s "
        "resolved_tenant_id=%s access_token_present=%s",
        current_user.get("id"),
        resolve_auth_tenant_id(current_user),
        resolve_auth_enterprise_id(current_user),
        tenant_uuid,
        bool(access_token),
    )

    resolved = None
    if tenant_uuid is not None:
        resolved = _active_config_for_tenant(db, tenant_uuid)
    else:
        global_config = (
            db.query(ProgramFormConfiguration)
            .filter(
                ProgramFormConfiguration.scope == "global",
                ProgramFormConfiguration.is_active.is_(True),
                ProgramFormConfiguration.status == "published",
            )
            .order_by(ProgramFormConfiguration.published_at.desc().nullslast())
            .first()
        )
        if global_config:
            version = _get_published_version(db, global_config.id)
            if version:
                resolved = (global_config, version)

    if not resolved:
        logger.info("Program active-form resolve: no active configuration found for tenant_id=%s", tenant_uuid)
        raise HTTPException(status_code=404, detail="No active Program form configuration found")
    config, version = resolved
    logger.info(
        "Program active-form resolved: tenant_id=%s -> configuration_id=%s scope=%s",
        tenant_uuid, config.id, config.scope,
    )
    return build_active_response(config, version)


def get_program_form_configuration_service(db: Session, program_id: UUID, current_user: dict) -> dict:
    from app.repository.program_repo import get_program_by_id

    program = get_program_by_id(db, program_id)
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")
    if program.form_configuration_version_id:
        version = (
            db.query(ProgramFormConfigurationVersion)
            .filter(ProgramFormConfigurationVersion.id == program.form_configuration_version_id)
            .first()
        )
        if version:
            config = db.query(ProgramFormConfiguration).filter(ProgramFormConfiguration.id == version.configuration_id).first()
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


def validate_form_required_core_fields(program_data, sections: list[dict], custom_values_map: dict | None = None) -> None:
    payload = program_data.to_model_data() if hasattr(program_data, "to_model_data") else (
        program_data.model_dump() if hasattr(program_data, "model_dump") else dict(program_data)
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
) -> tuple[ProgramFormConfiguration, ProgramFormConfigurationVersion]:
    if version_id:
        version = db.query(ProgramFormConfigurationVersion).filter(ProgramFormConfigurationVersion.id == version_id).first()
        if not version or version.status != "published":
            raise HTTPException(status_code=400, detail="form_configuration_version_id must reference a published version")
        config = db.query(ProgramFormConfiguration).filter(ProgramFormConfiguration.id == version.configuration_id).first()
        if not config or not config.is_active or config.status != "published":
            raise HTTPException(status_code=400, detail="Form configuration is not active/published")
        if config.scope == "selective":
            assignment = db.query(ProgramFormAssignment).filter(
                ProgramFormAssignment.configuration_id == config.id,
                ProgramFormAssignment.tenant_id == tenant_id,
            ).first()
            if not assignment:
                raise HTTPException(status_code=403, detail="Form configuration not assigned to this tenant")
            if enterprise_id and assignment.enterprise_id and str(assignment.enterprise_id) != str(enterprise_id):
                raise HTTPException(status_code=400, detail="Enterprise does not match form assignment")
        return config, version

    resolved = _active_config_for_tenant(db, tenant_id)
    if not resolved:
        raise HTTPException(status_code=404, detail="No active Program form configuration found")
    return resolved


def apply_form_configuration_to_program_data(db: Session, program_data, current_user: dict) -> dict:
    """Validate + return form_configuration_* columns and normalized custom_values."""
    if current_user.get("role") not in ("admin", "super_admin", "provider"):
        raise HTTPException(status_code=403, detail="Enterprise Admin access required")

    tenant_id = resolve_auth_tenant_id_with_db(db, current_user)
    enterprise_id = getattr(program_data, "enterprise_id", None)

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
        payload_tenant = getattr(program_data, "tenant_id", None)
        if not tenant_id and payload_tenant:
            tenant_id = payload_tenant
        if not tenant_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    "tenant_id could not be resolved from the WebAuth session or Program payload "
                    "(send enterprise_id or tenant_id when the session token has no tenant claim)"
                ),
            )
        ent = _resolve_enterprise_for_tenant(db, UUID(str(tenant_id)))
        if not ent:
            raise HTTPException(status_code=404, detail="No enterprise found for authenticated tenant")
        program_data.enterprise_id = ent.id
        enterprise_id = ent.id

    tenant_uuid = UUID(str(tenant_id)) if tenant_id else (ent.tenant_id if ent else None)
    if not tenant_uuid:
        raise HTTPException(status_code=400, detail="tenant_id is required for form configuration resolution")

    if not getattr(program_data, "tenant_id", None):
        program_data.tenant_id = tenant_uuid

    version_id = getattr(program_data, "form_configuration_version_id", None)
    config, version = resolve_version_for_create(
        db,
        version_id=version_id,
        tenant_id=tenant_uuid,
        enterprise_id=enterprise_id,
    )
    sections = normalize_sections(version.sections or [], assign_ids=False)

    raw_custom = getattr(program_data, "custom_values", None)
    custom_map: dict = {}
    if isinstance(raw_custom, dict):
        custom_map = {str(k): v for k, v in raw_custom.items()}

    validate_form_required_core_fields(program_data, sections, custom_map)
    custom_values = validate_custom_values(raw_custom, sections)
    return {
        "form_configuration_id": config.id,
        "form_configuration_version_id": version.id,
        "custom_values": custom_values,
        "tenant_id": tenant_uuid,
        "enterprise_id": enterprise_id,
    }


def apply_form_configuration_to_program_update(db: Session, program, update_data, current_user: dict) -> dict | None:
    if getattr(update_data, "custom_values", None) is None:
        return None
    version_id = program.form_configuration_version_id
    if not version_id:
        return {"custom_values": validate_custom_values(update_data.custom_values, [])}
    version = db.query(ProgramFormConfigurationVersion).filter(ProgramFormConfigurationVersion.id == version_id).first()
    if not version:
        raise HTTPException(status_code=400, detail="Program linked to missing form configuration version")
    sections = normalize_sections(version.sections or [], assign_ids=False)
    return {"custom_values": validate_custom_values(update_data.custom_values, sections)}
