"""Production stability audit: GET /api/v1/enterprises/ (named directly in the
reported ECONNRESET) issued 2-3 extra queries PER ENTERPRISE on the page
(business hours, reviews, distance) via enrich_enterprise_list_fields called
once per item inside map_enterprise_list_item's list comprehension — the same
N+1 anti-pattern class already found and fixed in the Chat module. A 20-item
page issued roughly 40-60+ SQL statements. Batched into a fixed handful of
queries regardless of page size, mirroring the Chat fix's approach.

These tests prove the batched functions return results identical to the
original per-item functions, and that query count no longer scales with the
number of enterprises.
"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.models.attribute_model import DynamicAttribute
from app.models.location_model import EnterpriseLocation
from app.models.service_model import Service
from app.services import catalog_enrichment as enrich


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, 'visit_JSONB', lambda *a, **kw: 'JSON', raising=False)
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[Service.__table__, DynamicAttribute.__table__, EnterpriseLocation.__table__])
    session = sessionmaker(bind=engine)()
    yield session, engine
    session.close()
    engine.dispose()


def _service(enterprise_id, schedule):
    return Service(
        id=uuid4(), enterprise_id=enterprise_id, service_name="Massage", service_category="Wellness",
        service_price=10.0, duration=30, status="active", availability_schedule=schedule,
    )


def _review_attribute(entity_id, reviews_json):
    return DynamicAttribute(
        id=uuid4(), entity_type="enterprise", entity_id=entity_id,
        attribute_name="reviews", attribute_value=reviews_json, attribute_type="json",
        is_deleted=False,
    )


class _QueryCounter:
    def __init__(self, engine):
        self.count = 0
        self.engine = engine

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._on_execute)

    def _on_execute(self, *a, **k):
        self.count += 1


def test_business_hours_batch_matches_per_item_results(db):
    session, engine = db
    e1, e2 = uuid4(), uuid4()
    session.add_all([
        _service(e1, [{"day": "monday", "start_time": "09:00", "end_time": "17:00", "is_available": True}]),
        _service(e2, [{"day": "tuesday", "start_time": "10:00", "end_time": "18:00", "is_available": True}]),
    ])
    session.commit()

    expected_e1 = enrich.aggregate_business_hours(session, e1)
    expected_e2 = enrich.aggregate_business_hours(session, e2)

    batched = enrich.aggregate_business_hours_batch(session, [e1, e2])
    assert batched[e1] == expected_e1
    assert batched[e2] == expected_e2


def test_business_hours_batch_enterprise_with_no_services_returns_all_closed(db):
    session, engine = db
    e1 = uuid4()
    result = enrich.aggregate_business_hours_batch(session, [e1])
    assert all(day["is_closed"] for day in result[e1])


def test_reviews_batch_matches_per_item_results(db):
    session, engine = db
    e1, e2 = uuid4(), uuid4()
    session.add_all([
        _review_attribute(e1, '[{"id": "r1", "rating": 5, "comment": "Great"}]'),
        _review_attribute(e2, '[{"id": "r2", "rating": 3, "comment": "OK"}, {"id": "r3", "rating": 4}]'),
    ])
    session.commit()

    expected_e1 = enrich.get_catalog_reviews(session, "enterprise", e1)
    expected_e2 = enrich.get_catalog_reviews(session, "enterprise", e2)

    batched = enrich.get_catalog_reviews_batch(session, "enterprise", [e1, e2])
    assert batched[e1] == expected_e1
    assert batched[e2] == expected_e2
    assert batched[e2][1] == 2


def test_distance_batch_matches_per_item_results(db):
    session, engine = db
    e1, e2 = uuid4(), uuid4()
    session.add_all([
        EnterpriseLocation(id=uuid4(), enterprise_id=e1, location_name="HQ", latitude=40.7128, longitude=-74.0060),
        EnterpriseLocation(id=uuid4(), enterprise_id=e2, location_name="Branch", latitude=34.0522, longitude=-118.2437),
    ])
    session.commit()

    user_lat, user_lng = 40.0, -75.0
    expected_e1 = enrich.nearest_location_distance_miles(session, e1, user_lat, user_lng)
    expected_e2 = enrich.nearest_location_distance_miles(session, e2, user_lat, user_lng)

    batched = enrich.nearest_location_distance_miles_batch(session, [e1, e2], user_lat, user_lng)
    assert batched[e1] == expected_e1
    assert batched[e2] == expected_e2


def test_distance_batch_returns_none_for_all_when_no_lat_lng(db):
    session, engine = db
    ids = [uuid4(), uuid4()]
    result = enrich.nearest_location_distance_miles_batch(session, ids, None, None)
    assert result == {ids[0]: None, ids[1]: None}


def test_enrich_enterprise_list_fields_batch_matches_per_item(db):
    session, engine = db
    e1, e2 = uuid4(), uuid4()
    session.add_all([
        _service(e1, [{"day": "monday", "start_time": "09:00", "end_time": "17:00", "is_available": True}]),
        _review_attribute(e1, '[{"id": "r1", "rating": 5}]'),
        EnterpriseLocation(id=uuid4(), enterprise_id=e1, location_name="HQ", latitude=40.7128, longitude=-74.0060),
    ])
    session.commit()

    expected_e1 = enrich.enrich_enterprise_list_fields(session, e1, user_lat=40.0, user_lng=-75.0)
    expected_e2 = enrich.enrich_enterprise_list_fields(session, e2, user_lat=40.0, user_lng=-75.0)

    batched = enrich.enrich_enterprise_list_fields_batch(session, [e1, e2], user_lat=40.0, user_lng=-75.0)
    assert batched[e1] == expected_e1
    assert batched[e2] == expected_e2


def test_query_count_does_not_scale_with_enterprise_count(db):
    session, engine = db
    small = [uuid4(), uuid4()]
    for eid in small:
        session.add(_service(eid, [{"day": "monday", "start_time": "09:00", "end_time": "17:00", "is_available": True}]))
        session.add(_review_attribute(eid, '[{"id": "r", "rating": 5}]'))
    session.commit()

    with _QueryCounter(engine) as counter:
        enrich.enrich_enterprise_list_fields_batch(session, small, user_lat=40.0, user_lng=-75.0)
    small_count = counter.count

    large = [uuid4() for _ in range(20)]
    for eid in large:
        session.add(_service(eid, [{"day": "monday", "start_time": "09:00", "end_time": "17:00", "is_available": True}]))
        session.add(_review_attribute(eid, '[{"id": "r", "rating": 5}]'))
    session.commit()

    with _QueryCounter(engine) as counter2:
        enrich.enrich_enterprise_list_fields_batch(session, small + large, user_lat=40.0, user_lng=-75.0)
    large_count = counter2.count

    # Exactly 3 queries (business hours, reviews, locations) regardless of how
    # many enterprise IDs are passed — not 3 per enterprise.
    assert small_count == 3
    assert large_count == 3
