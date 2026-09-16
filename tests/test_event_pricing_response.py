from datetime import datetime

import pytest

from app.models.event_model import Event
from app.repository.event_repo import create_event, update_event
from app.schemas.event_schema import EventCreate, EventUpdate, EventResponse, EventDetailResponse, EventListItemResponse
from app.services.response_mappers import map_event_write, map_event_detail, map_event_list_item
from tests.test_event_venue_roundtrip import sessions


def assert_pricing_responses(event, expected):
    for mapper, schema in ((map_event_write, EventResponse), (map_event_detail, EventDetailResponse),
                           (map_event_list_item, EventListItemResponse)):
        result = schema.model_validate(mapper(event)).model_dump(mode="json")
        assert result["pricing_type"] == expected


@pytest.mark.parametrize("pricing_type", ["free", "paid"])
def test_pricing_type_in_create_update_detail_and_list(sessions, pricing_type):
    with sessions() as db:
        event = create_event(db, EventCreate(title="Event", category="Wellness",
            start_date=datetime(2026, 10, 1), end_date=datetime(2026, 10, 2),
            pricing_type=pricing_type, price="100" if pricing_type == "paid" else None))
        event_id = event.id
        assert_pricing_responses(event, pricing_type)
        # Omitting pricing fields in a partial edit must not reset paid to free.
        update_event(db, event, EventUpdate(title="Edited event"))
    with sessions() as db:
        event = db.get(Event, event_id)
        assert_pricing_responses(event, pricing_type)
        new_type = "free" if pricing_type == "paid" else "paid"
        update_event(db, event, EventUpdate(pricing_type=new_type, price="200" if new_type == "paid" else None))
        assert_pricing_responses(event, new_type)
    with sessions() as db:
        assert_pricing_responses(db.get(Event, event_id), new_type)
