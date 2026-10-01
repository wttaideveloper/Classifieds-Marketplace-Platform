"""Event Type master configuration (Phase 2.2, evolved): the backend-authoritative list of Event Types
and their module rules, replacing the Phase 2.2 hardcoded ``EVENT_TYPE_DEFAULT_MODULES`` table.

Global, platform-level reference data — no tenant scoping, same shape as ``EventCategory``. Referenced
by ``events.event_type`` (a plain string column, matched by ``key``; deliberately NOT a foreign key —
see ``docs/event-type-modules-contract.md`` and the Phase 2.2 migration for why: it keeps every existing
event and every prior Phase 2 migration exactly as it is, and lets an Event Type be deactivated or even
deleted-when-unused without any risk to already-created events, which only ever read their own already-
persisted ``event_type``/``modules`` columns).
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.database import Base


class EventTypeConfig(Base):
    __tablename__ = "event_types"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Stable, admin-curated identifier events.event_type stores (app.utils.event_modules.EVENT_TYPE_KEY_PATTERN:
    # lowercase snake_case, max 30 chars). Immutable once created — see EventTypeUpdate, which has no `key` field.
    key = Column(String(30), nullable=False, unique=True, index=True)
    name = Column(String(100), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    # Each is a full 8-key {module_name: bool} dict — the same shape as Event.modules / EventModules.
    # default_modules : applied once, to a NEW event of this type (never re-applied on read).
    # allowed_modules : which modules an event of this type may enable at all.
    # required_modules: which modules an event of this type may never disable.
    # Invariants (enforced by the schema, not the DB): required[k] -> default[k] and default[k] -> allowed[k].
    default_modules = Column(JSONB, nullable=False)
    allowed_modules = Column(JSONB, nullable=False)
    required_modules = Column(JSONB, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
