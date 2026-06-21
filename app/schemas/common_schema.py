from typing import Literal

from pydantic import BaseModel, Field

EnterpriseStatusLabel = Literal["active", "inactive", "pending"]

DEFAULT_AVAILABILITY: dict = {
    "week_dates": [],
    "day_wise_slot_count": {},
    "slot_timings": [],
}


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
