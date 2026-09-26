"""Request/response schemas for Event accommodation (Phase 2.7).

Accommodation is configuration (an option list on the Event) plus a list of selected option ids on a registration, like
meals (Phase 2.6). Whether accommodation is on is ``modules.accommodation`` and nowhere else:
``EventAccommodation.enabled`` is a read-only mirror of it, and ``EventAccommodationInput.enabled`` is accepted only so
an edit form can echo an event back; it must agree with ``modules.accommodation`` or the request is rejected.
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, field_validator

from app.utils.event_accommodation import (
    ACCOMMODATION_ID_PATTERN,
    DESCRIPTION_MAX_LENGTH,
    MAX_INPUT_OPTIONS,
    NAME_MAX_LENGTH,
)

# An accommodation option id: a uuid4 string or a readable slug ("shared-room"). Malformed ids are rejected by validation.
AccommodationId = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=ACCOMMODATION_ID_PATTERN.pattern)]
MAX_SELECTIONS = 100


def reject_duplicate_accommodation_selections(values: list[str] | None) -> list[str] | None:
    if values is None:
        return values
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"duplicate accommodation selection: {value}")
        seen.add(value)
    return values


# --------------------------------------------------------------------------- configuration input


class AccommodationOptionInput(BaseModel):
    """One accommodation option. ``id`` is optional: send it to keep an option's identity across updates; without it the
    server reuses the id of the existing option with the same name, or generates a new one."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"id": "shared-room", "name": "Shared Room", "description": "Shared accommodation"}},
    )

    id: AccommodationId | None = Field(
        None, description="Stable option id (letters, digits, '_' and '-'; max 64). Omit for a new option."
    )
    name: str = Field(..., min_length=1, max_length=NAME_MAX_LENGTH, description="Label shown to attendees.")
    description: str | None = Field(None, max_length=DESCRIPTION_MAX_LENGTH)
    active: StrictBool = Field(True, description="false retires the option: it stays for history but can no longer be selected.")

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "description": self.description, "active": self.active}


class EventAccommodationInput(BaseModel):
    """Accommodation configuration on Event create/update. ``options`` is the desired set of ACTIVE options: options an
    update does not list are retired (kept with ``active: false``), never deleted. null/omitted ``options`` = no change."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"options": [{"name": "Shared Room"}, {"name": "Private Room"}]}},
    )

    enabled: StrictBool | None = Field(
        None,
        description="Read-only mirror of modules.accommodation. Optional; if sent it must equal modules.accommodation. "
                    "Use modules.accommodation to switch accommodation on or off.",
    )
    options: list[AccommodationOptionInput] | None = Field(None, max_length=MAX_INPUT_OPTIONS)

    def option_dicts(self) -> list[dict] | None:
        return None if self.options is None else [option.as_dict() for option in self.options]


# --------------------------------------------------------------------------- responses


class AccommodationOption(BaseModel):
    id: str
    name: str
    description: str | None = None
    active: bool = Field(..., description="false = retired by the organizer: kept for history, not selectable.")


class EventAccommodation(BaseModel):
    enabled: bool = Field(..., description="Mirrors modules.accommodation (the single switch). Legacy events: false.")
    options: list[AccommodationOption] = Field(default_factory=list)


class AttendeeAccommodationSelection(BaseModel):
    accommodation_id: str
    name: str | None = Field(
        None, description="The option's name; null only if the id is not (or no longer) in the event's configuration."
    )
    active: bool = Field(..., description="false when the organizer has since retired the option.")


# --------------------------------------------------------------------------- selections


class EventAccommodationSelectionUpdate(BaseModel):
    """Replace a registration's accommodation selections. An empty list clears them."""

    model_config = ConfigDict(
        extra="forbid", json_schema_extra={"example": {"accommodation_selections": ["shared-room"]}}
    )

    accommodation_selections: list[AccommodationId] = Field(
        ..., max_length=MAX_SELECTIONS, description="Accommodation option ids (each at most once)."
    )

    @field_validator("accommodation_selections")
    @classmethod
    def _no_duplicates(cls, values: list[str]) -> list[str]:
        return reject_duplicate_accommodation_selections(values)


class EventAccommodationSelectionResponse(BaseModel):
    event_id: UUID
    registration_id: UUID
    accommodation_selections: list[AttendeeAccommodationSelection] = Field(default_factory=list)


class DashboardAccommodation(BaseModel):
    accommodation_id: str
    name: str
    selected_count: int = Field(..., description="Active registrations (confirmed/attended) that selected this option.")
    active: bool = Field(..., description="Retired options are listed only while at least one registration still holds them.")
