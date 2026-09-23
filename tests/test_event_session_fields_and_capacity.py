"""Event Session description/speaker_bio contract (E-session-fields) and the
Event capacity relationship validator (min_participants <= max_participants <=
capacity), added to close a gap the Web Form Builder already assumed
client-side but the backend never enforced. See app/schemas/event_schema.py."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from app.models.event_model import Event
from app.repository.event_repo import create_event, update_event
from app.schemas.event_schema import (
    EventCreate,
    EventResponse,
    EventSessionCreate,
    EventSessionUpdate,
    EventUpdate,
)
from app.services.event_service import add_session_service, update_session_service
from app.services.response_mappers import map_event_write
from tests.test_event_venue_roundtrip import sessions  # shared sqlite fixture


# ---- EventSessionCreate/Update — description & speaker_bio ----

def test_session_create_accepts_description_and_speaker_bio():
    s = EventSessionCreate(session_date="2026-10-01", title="Keynote",
                            description="Opening remarks", speaker="Ada Lovelace",
                            speaker_bio="Pioneer of computing")
    assert s.description == "Opening remarks"
    assert s.speaker_bio == "Pioneer of computing"


def test_session_create_without_description_is_valid():
    s = EventSessionCreate(session_date="2026-10-01", title="Keynote", speaker="Ada")
    assert s.description is None


def test_session_create_without_speaker_bio_is_valid():
    s = EventSessionCreate(session_date="2026-10-01", title="Keynote", speaker="Ada")
    assert s.speaker_bio is None


def test_existing_session_shape_without_new_fields_still_validates():
    # Exactly the shape the Web/Mobile clients already send today.
    s = EventSessionCreate(session_date="2026-10-01", title="Keynote", speaker="Ada",
                            start_time="10:00", end_time="11:00", location="Main Hall")
    assert s.description is None and s.speaker_bio is None


def test_session_update_accepts_description_and_speaker_bio():
    s = EventSessionUpdate(description="Updated agenda", speaker_bio="New bio")
    assert s.model_dump(exclude_unset=True) == {"description": "Updated agenda", "speaker_bio": "New bio"}


# ---- Dedicated session endpoints (add_session_service / update_session_service) ----

def test_add_session_service_persists_description_and_speaker_bio(sessions):
    with sessions() as db:
        event = create_event(db, EventCreate(title="Event", category="Wellness",
            start_date=datetime(2026, 10, 1), end_date=datetime(2026, 10, 2)))
        payload = EventSessionCreate(session_date="2026-10-01", title="Keynote",
                                      description="Opening remarks", speaker="Ada",
                                      speaker_bio="Pioneer of computing")
        new_session = add_session_service(db, event.id, payload)
        assert new_session["description"] == "Opening remarks"
        assert new_session["speaker_bio"] == "Pioneer of computing"

    with sessions() as fresh:
        persisted = fresh.get(Event, event.id)
        assert persisted.sessions[0]["description"] == "Opening remarks"
        assert persisted.sessions[0]["speaker_bio"] == "Pioneer of computing"


def test_update_session_service_sets_description_and_speaker_bio(sessions):
    with sessions() as db:
        event = create_event(db, EventCreate(title="Event", category="Wellness",
            start_date=datetime(2026, 10, 1), end_date=datetime(2026, 10, 2)))
        created = add_session_service(db, event.id, EventSessionCreate(session_date="2026-10-01", title="Keynote"))
        session_id = created["id"]

        updated = update_session_service(db, event.id, session_id,
                                          EventSessionUpdate(description="New desc", speaker_bio="New bio"))
        assert updated["description"] == "New desc"
        assert updated["speaker_bio"] == "New bio"
        # Fields not touched by the update stay as they were.
        assert updated["title"] == "Keynote"


# ---- Main Event create/update — sessions pass through untyped (already backward compatible) ----

def test_event_create_with_session_description_and_speaker_bio_round_trips(sessions):
    with sessions() as db:
        event = create_event(db, EventCreate(
            title="Event", category="Wellness",
            start_date=datetime(2026, 10, 1), end_date=datetime(2026, 10, 2),
            sessions=[{"session_date": "2026-10-01", "title": "Keynote",
                       "description": "Opening remarks", "speaker": "Ada",
                       "speaker_bio": "Pioneer of computing"}],
        ))
        result = EventResponse.model_validate(map_event_write(event)).model_dump(mode="json")
        assert result["sessions"][0]["description"] == "Opening remarks"
        assert result["sessions"][0]["speaker_bio"] == "Pioneer of computing"

    with sessions() as fresh:
        persisted = fresh.get(Event, event.id)
        assert persisted.sessions[0]["description"] == "Opening remarks"


def test_event_update_with_session_description_and_speaker_bio_round_trips(sessions):
    with sessions() as db:
        event = create_event(db, EventCreate(title="Event", category="Wellness",
            start_date=datetime(2026, 10, 1), end_date=datetime(2026, 10, 2)))
        update_event(db, event, EventUpdate(
            sessions=[{"session_date": "2026-10-01", "title": "Keynote",
                       "description": "Updated", "speaker_bio": "Bio"}],
        ))
        result = EventResponse.model_validate(map_event_write(event)).model_dump(mode="json")
        assert result["sessions"][0]["description"] == "Updated"
        assert result["sessions"][0]["speaker_bio"] == "Bio"


# ---- Capacity relationship: min_participants <= max_participants <= capacity ----

def test_capacity_bounds_valid_combination_is_accepted():
    e = EventCreate(title="Event", category="Wellness",
                     start_date=datetime(2026, 10, 1), end_date=datetime(2026, 10, 2),
                     min_participants="5", max_participants="20", capacity="30")
    assert e.min_participants == "5" and e.max_participants == "20" and e.capacity == "30"


def test_capacity_bounds_min_greater_than_max_is_rejected():
    with pytest.raises(ValidationError, match="min_participants must not exceed max_participants"):
        EventCreate(title="Event", category="Wellness",
                    start_date=datetime(2026, 10, 1), end_date=datetime(2026, 10, 2),
                    min_participants="20", max_participants="5")


def test_capacity_bounds_max_greater_than_capacity_is_rejected():
    with pytest.raises(ValidationError, match="max_participants must not exceed capacity"):
        EventCreate(title="Event", category="Wellness",
                    start_date=datetime(2026, 10, 1), end_date=datetime(2026, 10, 2),
                    max_participants="50", capacity="30")


@pytest.mark.parametrize("field", ["capacity", "min_participants", "max_participants"])
def test_capacity_bounds_negative_value_is_rejected(field):
    with pytest.raises(ValidationError, match="must not be negative"):
        EventCreate(title="Event", category="Wellness",
                    start_date=datetime(2026, 10, 1), end_date=datetime(2026, 10, 2),
                    **{field: "-1"})


def test_capacity_bounds_all_null_is_accepted():
    e = EventCreate(title="Event", category="Wellness",
                     start_date=datetime(2026, 10, 1), end_date=datetime(2026, 10, 2))
    assert e.capacity is None and e.min_participants is None and e.max_participants is None


def test_capacity_bounds_non_numeric_values_are_not_rejected():
    # Matches the existing runtime leniency in event_service.py (registration/
    # checkout wrap int(float(str(x))) in try/except ValueError and skip the
    # check) — this validator mirrors that instead of introducing a new, stricter
    # type rule that could reject legacy non-numeric data.
    e = EventCreate(title="Event", category="Wellness",
                     start_date=datetime(2026, 10, 1), end_date=datetime(2026, 10, 2),
                     capacity="TBD")
    assert e.capacity == "TBD"


def test_capacity_bounds_only_one_field_set_is_accepted():
    e = EventCreate(title="Event", category="Wellness",
                     start_date=datetime(2026, 10, 1), end_date=datetime(2026, 10, 2),
                     capacity="30")
    assert e.capacity == "30"
