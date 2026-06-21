from typing import Literal

from pydantic import BaseModel, Field

EnterpriseStatusLabel = Literal["active", "inactive", "pending"]


class AvailabilityResponse(BaseModel):
    week_dates: list[str] = Field(
        default_factory=list,
        description="Dates available in the current week.",
    )
    day_wise_slot_count: dict[str, int] = Field(
        default_factory=dict,
        description="Number of slots per day.",
    )
    slot_timings: list[str] = Field(
        default_factory=list,
        description="Available slot time ranges.",
    )


class AvailabilityScheduleEntry(BaseModel):
    day: str = Field(
        ...,
        description="Day of the week (e.g. monday, tuesday).",
        examples=["monday"],
    )
    is_available: bool = Field(
        True,
        description="Whether the service is offered on this day.",
    )
    start_time: str = Field(
        ...,
        description="Start time in HH:MM format.",
        examples=["09:00"],
    )
    end_time: str = Field(
        ...,
        description="End time in HH:MM format.",
        examples=["17:00"],
    )
    slot_length: str = Field(
        ...,
        description="Slot duration in minutes.",
        examples=["60"],
    )
