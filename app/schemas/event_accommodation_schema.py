"""Request/response schemas for Event accommodation (Phase 2.7).

Accommodation is configuration (an option list on the Event) plus a list of selected option ids on a registration, like
meals (Phase 2.6). Whether accommodation is on is ``modules.accommodation`` and nowhere else:
``EventAccommodation.enabled`` is a read-only mirror of it, and ``EventAccommodationInput.enabled`` is accepted only so
an edit form can echo an event back; it must agree with ``modules.accommodation`` or the request is rejected.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, field_validator, model_validator

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
    price: Decimal | None = Field(None, ge=0, description="Price per selection. Omit/null = free (0).")
    currency: str | None = Field(None, max_length=10, description="Omit to use the event's own currency.")
    capacity: int | None = Field(None, ge=0, description="Maximum number of attendees who may hold this option. Omit/null = unlimited.")
    purchase_start_at: datetime | None = Field(None, description="When attendees may start selecting this option. Omit = always open.")
    purchase_end_at: datetime | None = Field(None, description="When attendees may no longer newly select this option. Omit = always open.")
    service_start_at: datetime | None = Field(None, description="Stay/service start — informational/fulfilment only.")
    service_end_at: datetime | None = Field(None, description="Informational/fulfilment only.")
    # Server-computed (AccommodationOption response only, never stored — see as_dict()). Accepted-and-ignored
    # here, not rejected — see event_meal_schema.MealOptionInput, identical reasoning.
    reserved_count: int | None = Field(None, description="Ignored on input — read-only, see AccommodationOption.")
    remaining_capacity: int | None = Field(None, description="Ignored on input — read-only, see AccommodationOption.")
    sold_out: bool | None = Field(None, description="Ignored on input — read-only, see AccommodationOption.")

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @model_validator(mode="after")
    def _valid_windows(self):
        if self.purchase_start_at and self.purchase_end_at and self.purchase_start_at >= self.purchase_end_at:
            raise ValueError("purchase_start_at must be before purchase_end_at")
        if self.service_start_at and self.service_end_at and self.service_start_at >= self.service_end_at:
            raise ValueError("service_start_at must be before service_end_at")
        return self

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "active": self.active,
            "price": str(self.price) if self.price is not None else None,
            "currency": self.currency,
            "capacity": self.capacity,
            "purchase_start_at": self.purchase_start_at.isoformat() if self.purchase_start_at else None,
            "purchase_end_at": self.purchase_end_at.isoformat() if self.purchase_end_at else None,
            "service_start_at": self.service_start_at.isoformat() if self.service_start_at else None,
            "service_end_at": self.service_end_at.isoformat() if self.service_end_at else None,
        }


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
    price: float = Field(0, description="Price per selection. 0 for options created before pricing existed.")
    currency: str = Field("INR", description="Resolved currency — the option's own, or the event's if unset.")
    capacity: int | None = Field(None, description="Maximum selections allowed. null = unlimited.")
    reserved_count: int | None = Field(None, description="Confirmed/attended registrations currently holding this option. null when capacity is unlimited (not computed).")
    remaining_capacity: int | None = Field(None, description="null = unlimited.")
    sold_out: bool = Field(False)
    purchase_start_at: str | None = None
    purchase_end_at: str | None = None
    service_start_at: str | None = None
    service_end_at: str | None = None


class EventAccommodation(BaseModel):
    enabled: bool = Field(..., description="Mirrors modules.accommodation (the single switch). Legacy events: false.")
    options: list[AccommodationOption] = Field(default_factory=list)


class AttendeeAccommodationSelection(BaseModel):
    accommodation_id: str
    name: str | None = Field(
        None, description="The option's name; null only if the id is not (or no longer) in the event's configuration."
    )
    active: bool = Field(..., description="false when the organizer has since retired the option.")
    price: float | None = Field(None, description="The price actually charged at purchase time, never the option's current price.")
    currency: str | None = None
    service_start_at: str | None = Field(None, description="Informational/fulfilment only — stay/service window.")
    service_end_at: str | None = None
    status: str = Field("selected", description="'confirmed' when a paid EventRegistrationOption backs this selection, otherwise 'selected' (free/legacy).")


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
    selected_count: int = Field(..., description="Active registrations (confirmed/attended) that selected this option. Kept for backward compatibility — identical to reserved_count.")
    active: bool = Field(..., description="Retired options are listed only while at least one registration still holds them.")
    price: float = Field(0)
    currency: str = Field("INR")
    capacity: int | None = None
    reserved_count: int | None = None
    remaining_capacity: int | None = None
    sold_out: bool = False
