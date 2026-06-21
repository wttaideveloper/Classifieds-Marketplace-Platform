from typing import Literal

from pydantic import BaseModel, Field

EnterpriseStatusLabel = Literal["active", "inactive", "pending"]


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


class ServiceAvailabilityDay(BaseModel):
    day: str = Field(
        ...,
        description="Day name with capitalized label.",
        examples=["Monday"],
    )
    date: str = Field(
        ...,
        description="Calendar date for this day in the current week (YYYY-MM-DD).",
        examples=["2026-06-22"],
    )
    slots: list[str] = Field(
        default_factory=list,
        description="Time slot ranges for the day.",
        examples=[["09:00-10:00", "10:00-11:00"]],
    )
