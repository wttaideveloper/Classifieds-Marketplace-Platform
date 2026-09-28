"""Event Type master configuration (Phase 2.2, evolved): request/response schemas for
``/api/v1/event-types``.

Event Types are backend-authoritative reference data (see ``app/models/event_type_model.py``). This
module reuses the existing Phase 2.2 ``EventModules`` shape (all eight module keys, always present,
always boolean) for ``default_modules``/``allowed_modules``/``required_modules`` — no second module
representation is introduced.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, model_validator

from app.schemas.event_schema import EventModules
from app.utils.event_modules import EVENT_MODULE_KEYS, EVENT_TYPE_KEY_MAX_LENGTH, EVENT_TYPE_KEY_PATTERN

# A stable Event Type key: lowercase snake_case, starting with a letter, max 30 characters.
EventTypeKey = Annotated[
    str, StringConstraints(min_length=1, max_length=EVENT_TYPE_KEY_MAX_LENGTH, pattern=EVENT_TYPE_KEY_PATTERN.pattern)
]

_ALL_ALLOWED = {key: True for key in EVENT_MODULE_KEYS}
_NONE_REQUIRED = {key: False for key in EVENT_MODULE_KEYS}


def _check_module_map_consistency(default_modules: EventModules, allowed_modules: EventModules, required_modules: EventModules) -> None:
    """required[k] -> default[k] -> allowed[k]: a module can only be required if it is also defaulted
    on, and can only be defaulted on (or required) if it is also allowed. Keeps the three maps from
    contradicting each other so every event created under this type starts in a valid configuration."""
    default = default_modules.model_dump()
    allowed = allowed_modules.model_dump()
    required = required_modules.model_dump()
    contradicts_allowed = sorted(key for key in EVENT_MODULE_KEYS if (default[key] or required[key]) and not allowed[key])
    if contradicts_allowed:
        raise ValueError(
            f"allowed_modules must permit every module that is default-on or required: {', '.join(contradicts_allowed)}"
        )
    required_but_not_default = sorted(key for key in EVENT_MODULE_KEYS if required[key] and not default[key])
    if required_but_not_default:
        raise ValueError(
            f"required_modules must also be on in default_modules: {', '.join(required_but_not_default)}"
        )


class EventTypeCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "key": "prayer_meeting",
                "name": "Prayer Meeting",
                "active": True,
                "default_modules": {
                    "registration": True, "tickets": False, "sessions": False, "check_in": True,
                    "online_meeting": False, "custom_questions": False, "meals": False, "accommodation": False,
                },
                "allowed_modules": {
                    "registration": True, "tickets": False, "sessions": False, "check_in": True,
                    "online_meeting": False, "custom_questions": True, "meals": True, "accommodation": False,
                },
                "required_modules": {"registration": True},
            }
        },
    )

    key: EventTypeKey = Field(..., description="Stable identifier events.event_type stores. Immutable once created.")
    name: str = Field(..., min_length=1, max_length=100, description="Display name.")
    active: StrictBool = Field(True, description="Inactive types are not selectable for new events.")
    default_modules: EventModules = Field(..., description="Applied once to a NEW event of this type.")
    allowed_modules: EventModules | None = Field(
        None, description="Which modules an event of this type may enable. Omit to allow all eight modules."
    )
    required_modules: EventModules | None = Field(
        None, description="Which modules an event of this type may never disable. Omit to require none."
    )

    @model_validator(mode="after")
    def _apply_defaults_and_validate(self):
        if self.allowed_modules is None:
            self.allowed_modules = EventModules(**_ALL_ALLOWED)
        if self.required_modules is None:
            self.required_modules = EventModules(**_NONE_REQUIRED)
        _check_module_map_consistency(self.default_modules, self.allowed_modules, self.required_modules)
        return self


class EventTypeUpdate(BaseModel):
    """Partial update. ``key`` is intentionally not editable once an Event Type exists (Events reference
    it by value, not by id)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=100)
    active: StrictBool | None = None
    default_modules: EventModules | None = None
    allowed_modules: EventModules | None = None
    required_modules: EventModules | None = None


class EventTypeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: str
    name: str
    active: bool
    default_modules: EventModules
    allowed_modules: EventModules
    required_modules: EventModules
    created_at: datetime
    updated_at: datetime
