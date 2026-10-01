"""Request/response schemas for Event meals (Phase 2.6).

Meals are configuration (an option list on the Event) plus a list of selected option ids on a registration.
Whether meals are on is ``modules.meals`` and nowhere else: ``EventMeals.enabled`` is a read-only mirror of it, and
``EventMealsInput.enabled`` is accepted only so an edit form can echo an event back; it must agree with
``modules.meals`` or the request is rejected.
"""
from __future__ import annotations

from datetime import date as date_type, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, field_validator, model_validator

from app.utils.event_meals import DESCRIPTION_MAX_LENGTH, MAX_INPUT_OPTIONS, MEAL_ID_PATTERN, NAME_MAX_LENGTH

# A meal option id: a uuid4 string or a readable slug ("breakfast-day-1"). Malformed ids are rejected by validation.
MealId = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=MEAL_ID_PATTERN.pattern)]
MAX_SELECTIONS = 100


def reject_duplicate_selections(values: list[str] | None) -> list[str] | None:
    if values is None:
        return values
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"duplicate meal selection: {value}")
        seen.add(value)
    return values


# --------------------------------------------------------------------------- configuration input


class MealOptionInput(BaseModel):
    """One meal option. ``id`` is optional: send it to keep an option's identity across updates; without it the
    server reuses the id of the existing option with the same name and date, or generates a new one."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"id": "breakfast-day-1", "name": "Breakfast", "date": "2026-10-05", "description": "Served 7-9am"}},
    )

    id: MealId | None = Field(None, description="Stable option id (letters, digits, '_' and '-'; max 64). Omit for a new option.")
    name: str = Field(..., min_length=1, max_length=NAME_MAX_LENGTH, description="Label shown to attendees.")
    description: str | None = Field(None, max_length=DESCRIPTION_MAX_LENGTH)
    date: date_type | None = Field(None, description="Optional day the meal is served (YYYY-MM-DD).")
    active: StrictBool = Field(True, description="false retires the option: it stays for history but can no longer be selected.")
    price: Decimal | None = Field(None, ge=0, description="Price per selection. Omit/null = free (0).")
    currency: str | None = Field(None, max_length=10, description="Omit to use the event's own currency.")
    capacity: int | None = Field(None, ge=0, description="Maximum number of attendees who may hold this option. Omit/null = unlimited.")
    purchase_start_at: datetime | None = Field(None, description="When attendees may start selecting this option. Omit = always open.")
    purchase_end_at: datetime | None = Field(None, description="When attendees may no longer newly select this option. Omit = always open.")
    service_start_at: datetime | None = Field(None, description="When the meal is actually served — informational/fulfilment only.")
    service_end_at: datetime | None = Field(None, description="Informational/fulfilment only.")
    # Server-computed (MealOption response only, never stored — see as_dict()). Accepted-and-ignored here, not
    # rejected, so a client that GETs an event and PUTs the same JSON back (an edit form) is always a no-op,
    # never a 422 for "extra fields" on data the server itself produced.
    reserved_count: int | None = Field(None, description="Ignored on input — read-only, see MealOption.")
    remaining_capacity: int | None = Field(None, description="Ignored on input — read-only, see MealOption.")
    sold_out: bool | None = Field(None, description="Ignored on input — read-only, see MealOption.")

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
            "date": self.date.isoformat() if self.date else None,
            "active": self.active,
            "price": str(self.price) if self.price is not None else None,
            "currency": self.currency,
            "capacity": self.capacity,
            "purchase_start_at": self.purchase_start_at.isoformat() if self.purchase_start_at else None,
            "purchase_end_at": self.purchase_end_at.isoformat() if self.purchase_end_at else None,
            "service_start_at": self.service_start_at.isoformat() if self.service_start_at else None,
            "service_end_at": self.service_end_at.isoformat() if self.service_end_at else None,
        }


class EventMealsInput(BaseModel):
    """Meal configuration on Event create/update. ``options`` is the desired set of ACTIVE options: options an update
    does not list are retired (kept with ``active: false``), never deleted. null/omitted ``options`` = no change."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"options": [{"name": "Breakfast"}, {"name": "Lunch"}, {"name": "Dinner"}]}})

    enabled: StrictBool | None = Field(
        None, description="Read-only mirror of modules.meals. Optional; if sent it must equal modules.meals. Use modules.meals to switch meals on or off."
    )
    options: list[MealOptionInput] | None = Field(None, max_length=MAX_INPUT_OPTIONS)

    def option_dicts(self) -> list[dict] | None:
        return None if self.options is None else [option.as_dict() for option in self.options]


# --------------------------------------------------------------------------- responses


class MealOption(BaseModel):
    id: str
    name: str
    description: str | None = None
    date: str | None = None
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


class EventMeals(BaseModel):
    enabled: bool = Field(..., description="Mirrors modules.meals (the single switch). Legacy events: false.")
    options: list[MealOption] = Field(default_factory=list)


class AttendeeMealSelection(BaseModel):
    meal_id: str
    name: str | None = Field(None, description="The option's name; null only if the id is not (or no longer) in the event's configuration.")
    active: bool = Field(..., description="false when the organizer has since retired the option.")
    # Purchase-time snapshot (Phase 2.8) — null when this selection predates pricing/was never a paid line item
    # (e.g. a free option, or a legacy selection with no EventRegistrationOption row at all).
    price: float | None = Field(None, description="The price actually charged at purchase time, never the option's current price.")
    currency: str | None = None
    service_start_at: str | None = Field(None, description="Informational/fulfilment only — when this option is served.")
    service_end_at: str | None = None
    status: str = Field("selected", description="'confirmed' when a paid EventRegistrationOption backs this selection, otherwise 'selected' (free/legacy).")


# --------------------------------------------------------------------------- selections


class EventMealSelectionUpdate(BaseModel):
    """Replace a registration's meal selections. An empty list clears them."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"meal_selections": ["breakfast-day-1", "lunch-day-1"]}})

    meal_selections: list[MealId] = Field(..., max_length=MAX_SELECTIONS, description="Meal option ids (each at most once).")

    @field_validator("meal_selections")
    @classmethod
    def _no_duplicates(cls, values: list[str]) -> list[str]:
        return reject_duplicate_selections(values)


class EventMealSelectionResponse(BaseModel):
    event_id: UUID
    registration_id: UUID
    meal_selections: list[AttendeeMealSelection] = Field(default_factory=list)


class DashboardMeal(BaseModel):
    meal_id: str
    name: str
    selected_count: int = Field(..., description="Active registrations (confirmed/attended) that selected this option. Kept for backward compatibility — identical to reserved_count.")
    active: bool = Field(..., description="Retired options are listed only while at least one registration still holds them.")
    price: float = Field(0)
    currency: str = Field("INR")
    capacity: int | None = None
    reserved_count: int | None = None
    remaining_capacity: int | None = None
    sold_out: bool = False
