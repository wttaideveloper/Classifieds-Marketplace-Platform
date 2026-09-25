"""Request/response schemas for Event meals (Phase 2.6).

Meals are configuration (an option list on the Event) plus a list of selected option ids on a registration.
Whether meals are on is ``modules.meals`` and nowhere else: ``EventMeals.enabled`` is a read-only mirror of it, and
``EventMealsInput.enabled`` is accepted only so an edit form can echo an event back; it must agree with
``modules.meals`` or the request is rejected.
"""
from __future__ import annotations

from datetime import date as date_type
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, field_validator

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

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "date": self.date.isoformat() if self.date else None,
            "active": self.active,
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


class EventMeals(BaseModel):
    enabled: bool = Field(..., description="Mirrors modules.meals (the single switch). Legacy events: false.")
    options: list[MealOption] = Field(default_factory=list)


class AttendeeMealSelection(BaseModel):
    meal_id: str
    name: str | None = Field(None, description="The option's name; null only if the id is not (or no longer) in the event's configuration.")
    active: bool = Field(..., description="false when the organizer has since retired the option.")


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
    selected_count: int = Field(..., description="Active registrations (confirmed/attended) that selected this option.")
    active: bool = Field(..., description="Retired options are listed only while at least one registration still holds them.")
