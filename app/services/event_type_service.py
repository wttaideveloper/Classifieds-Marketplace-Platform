"""Event Type master configuration (Phase 2.2, evolved): CRUD service for ``/api/v1/event-types``.

Plain, un-versioned global reference data — same shape as the existing ``EventCategory`` CRUD
(``create_event_category_service`` etc. in ``app/services/event_service.py``): a duplicate ``key`` is
400, an unknown id is 404, and delete is refused (409) while any Event still references the key. There
is no tenant scoping (this is platform-level configuration) and no versioning/draft-publish workflow —
that machinery belongs to the separate Event Form Configuration system and is deliberately not
duplicated here.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.event_model import Event
from app.models.event_type_model import EventTypeConfig


def _get_or_404(db: Session, event_type_id: UUID) -> EventTypeConfig:
    row = db.query(EventTypeConfig).filter(EventTypeConfig.id == event_type_id).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event type not found")
    return row


def create_event_type_service(db: Session, payload) -> EventTypeConfig:
    existing = db.query(EventTypeConfig).filter(EventTypeConfig.key == payload.key).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Event type key '{payload.key}' already exists")
    row = EventTypeConfig(
        key=payload.key,
        name=payload.name.strip(),
        active=payload.active,
        default_modules=payload.default_modules.model_dump(),
        allowed_modules=payload.allowed_modules.model_dump(),
        required_modules=payload.required_modules.model_dump(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_event_types_service(db: Session, *, include_inactive: bool = False) -> list[EventTypeConfig]:
    query = db.query(EventTypeConfig)
    if not include_inactive:
        query = query.filter(EventTypeConfig.active.is_(True))
    return query.order_by(EventTypeConfig.name).all()


def get_event_type_service(db: Session, event_type_id: UUID) -> EventTypeConfig:
    return _get_or_404(db, event_type_id)


def update_event_type_service(db: Session, event_type_id: UUID, payload) -> EventTypeConfig:
    row = _get_or_404(db, event_type_id)
    fields = payload.model_fields_set
    default_modules = payload.default_modules.model_dump() if payload.default_modules is not None else dict(row.default_modules)
    allowed_modules = payload.allowed_modules.model_dump() if payload.allowed_modules is not None else dict(row.allowed_modules)
    required_modules = payload.required_modules.model_dump() if payload.required_modules is not None else dict(row.required_modules)
    if {"default_modules", "allowed_modules", "required_modules"} & fields:
        from app.schemas.event_schema import EventModules
        from app.schemas.event_type_schema import _check_module_map_consistency

        try:
            _check_module_map_consistency(EventModules(**default_modules), EventModules(**allowed_modules), EventModules(**required_modules))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    if "name" in fields and payload.name is not None:
        row.name = payload.name.strip()
    if "active" in fields and payload.active is not None:
        row.active = payload.active
    row.default_modules = default_modules
    row.allowed_modules = allowed_modules
    row.required_modules = required_modules
    db.commit()
    db.refresh(row)
    return row


def delete_event_type_service(db: Session, event_type_id: UUID) -> dict:
    row = _get_or_404(db, event_type_id)
    in_use = db.query(Event.id).filter(Event.event_type == row.key).first()
    if in_use is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete: event type '{row.key}' is used by at least one event. Deactivate it instead (active=false).",
        )
    db.delete(row)
    db.commit()
    return {"message": "Event type deleted"}
