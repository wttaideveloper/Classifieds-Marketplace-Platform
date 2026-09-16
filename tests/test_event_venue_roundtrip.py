from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.models.enterprise_model import Enterprise
from app.models.event_model import Event
from app.repository.event_repo import create_event, update_event
from app.schemas.event_schema import EventCreate, EventUpdate, EventVenue, EventResponse
from app.services.response_mappers import map_event_write
from app.services.event_template_mapping import validate_event_submission


VENUE = {"name": "zen ex hall", "address": "12, instant road", "city": "chennai"}


@pytest.fixture
def sessions(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *args, **kwargs: "JSON", raising=False)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Enterprise.__table__, Event.__table__])
    yield sessionmaker(bind=engine)
    engine.dispose()


@pytest.mark.parametrize("typed", [False, True], ids=["json-body", "typed-venue"])
def test_create_update_fresh_read_and_submission_preserve_name(sessions, typed):
    original = {**VENUE, "name": "Original hall"}
    with sessions() as db:
        event = create_event(db, EventCreate(title="Event", description="Description", category="Wellness",
            start_date=datetime(2026, 10, 1), end_date=datetime(2026, 10, 2),
            venue=EventVenue(**original) if typed else original))
        event_id = event.id
        assert EventResponse.model_validate(map_event_write(event)).venue["name"] == "Original hall"
        result = update_event(db, event, EventUpdate(venue=EventVenue(**VENUE) if typed else VENUE))
        returned = EventResponse.model_validate(map_event_write(result)).model_dump(mode="json")
        assert returned["venue"]["name"] == VENUE["name"]
        assert returned["venue"]["address"] == VENUE["address"]
        assert returned["venue"]["city"] == VENUE["city"]
    with sessions() as fresh:
        persisted = fresh.get(Event, event_id)
        assert persisted.venue["name"] == VENUE["name"]
        persisted.form_configuration_version_id = uuid4()
        validation_db = MagicMock()
        validation_db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(sections=[{
            "fields": [{"source":"core", "core_key":"venue", "required":True,
                        "composite_config":{"enabled_fields":["name","address","city"], "required_fields":["name"]}}]}])
        validate_event_submission(validation_db, persisted)
        persisted.venue = {"address": VENUE["address"], "city": VENUE["city"]}
        with pytest.raises(HTTPException, match="venue.name"):
            validate_event_submission(validation_db, persisted)


def test_partial_update_omits_venue_and_explicit_null_clears():
    assert "venue" not in EventUpdate(title="Changed title").to_model_data()
    assert EventUpdate(venue=None).to_model_data()["venue"] is None
