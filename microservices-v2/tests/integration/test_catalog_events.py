"""Catalog + events through the gateway vs. the monolith: an enterprise created in catalog reaches events
(replication push), events are created for it, published, registered for, searched, updated and deleted."""
import json

from flows import run_flow

TENANT = "7a7a7a7a-0000-4000-8000-000000000001"


def _field(key, label, renderer, pos):
    return {"source": "core", "core_key": key, "label": label, "renderer": renderer, "position": pos, "required": True, "is_enabled": True}


FORM = {"name": "Default event form", "scope": "global", "sections": [{
    "stable_key": "section_main", "label": "Event", "position": 1, "is_enabled": True,
    "fields": [_field("title", "Event Name", "text", 1), _field("description", "Description", "textarea", 2),
               _field("category", "Category", "select", 3), _field("start_date", "Start", "datetime", 4),
               _field("end_date", "End", "datetime", 5), _field("pricing_type", "Pricing", "select", 6)]}]}

STEPS = [
    {"as": "admin", "method": "POST", "path": "/api/v1/enterprises/", "expect": 201,
     "json": {"business_short_name": "Asha Events", "business_email": "events@example.com", "tenant_id": TENANT, "city": "Pune", "status": "active"}, "save": {"ent": "id"}},
    {"as": "admin", "method": "POST", "path": "/api/v1/enterprises/{ent}/locations", "json": {"location_name": "Hall A", "city": "Pune"}, "save": {"loc": "id"}},
    {"as": "super_admin", "method": "POST", "path": "/api/v1/admin/event-form-configurations/", "json": FORM, "save": {"cfg": "id"}},
    {"as": "super_admin", "method": "POST", "path": "/api/v1/admin/event-form-configurations/{cfg}/publish"},
    {"as": "super_admin", "method": "POST", "path": "/api/v1/admin/event-form-configurations/{cfg}/activate"},
    {"as": "admin", "method": "POST", "path": "/api/v1/events/", "expect": 201,
     "json": {"title": "Yoga Day", "description": "Morning yoga", "pricing_type": "free", "category": "wellness", "start_date": "2027-03-10T09:00:00", "end_date": "2027-03-10T12:00:00",
              "enterprise_id": "{ent}", "location_id": "{loc}", "delivery_mode": "in_person", "capacity": "50", "tenant_id": TENANT},
     "save": {"ev": "id"}},
    {"as": "admin", "method": "POST", "path": "/api/v1/events/",
     "json": {"title": "Online Talk", "description": "A talk", "pricing_type": "free", "category": "talks", "start_date": "2027-04-01T15:00:00", "end_date": "2027-04-01T16:00:00",
              "enterprise_id": "{ent}", "delivery_mode": "online", "meeting_provider": "zoom", "tenant_id": TENANT}, "save": {"ev2": "id"}},
    {"as": "admin", "method": "POST", "path": "/api/v1/events/", "json": {"title": "x", "category": "c", "start_date": "2027-01-02T10:00:00", "end_date": "2027-01-01T10:00:00", "enterprise_id": "{ent}"}},
    {"as": "admin", "method": "GET", "path": "/api/v1/events/{ev}"},
    {"as": None, "method": "GET", "path": "/api/v1/events/{ev}"},
    {"as": "admin", "method": "PATCH", "path": "/api/v1/events/{ev}/status", "json": {"status": "published"}},
    {"as": "super_admin", "method": "PATCH", "path": "/api/v1/events/{ev}/status", "json": {"status": "published"}},
    {"as": None, "method": "GET", "path": "/api/v1/events/{ev}"},
    {"as": None, "method": "GET", "path": "/api/v1/events/", "params": {"page": 1, "page_size": 5}},
    {"as": None, "method": "GET", "path": "/api/v1/search/events", "params": {"query": "yoga", "city": "Pune"}},
    {"as": "customer", "method": "POST", "path": "/api/v1/events/{ev}/registrations", "json": {"participant_name": "Ravi K", "participant_email": "ravi@example.com"}},
    {"as": "customer", "method": "POST", "path": "/api/v1/events/{ev}/registrations", "json": {"participant_name": "Ravi K", "participant_email": "ravi@example.com"}},
    {"as": "admin", "method": "GET", "path": "/api/v1/events/{ev}/registrations"},
    {"as": "admin", "method": "PUT", "path": "/api/v1/events/{ev}", "json": {"title": "Yoga Day 2027", "capacity": "60"}},
    {"as": "admin", "method": "PATCH", "path": "/api/v1/events/{ev2}/status", "json": {"status": "published"}},
    {"as": "super_admin", "method": "PATCH", "path": "/api/v1/events/{ev2}/status", "json": {"status": "published"}},
    {"as": "admin", "method": "DELETE", "path": "/api/v1/events/{ev2}"},
    {"as": "admin", "method": "GET", "path": "/api/v1/events/{ev2}"},
    {"as": "admin", "method": "PUT", "path": "/api/v1/enterprises/{ent}", "json": {"business_short_name": "Asha Events & Retreats"}},
    {"as": None, "method": "GET", "path": "/api/v1/events/{ev}"},
]


def test_catalog_and_events_together_behave_like_the_monolith(stack):
    import stubs

    monolith, gateway, direct = stack
    diffs, log, sides = run_flow(monolith, gateway, STEPS)
    assert not diffs, json.dumps(diffs, indent=1)
    statuses = [row[3] for row in log]
    assert statuses.count(200) + statuses.count(201) >= 14, log
    pushes = [c for c in stubs.CALLS if c[0] == "events" and c[2].startswith("/replica/")]
    assert pushes, "catalog must push enterprise/location changes to events"


def test_events_replica_matches_catalog_after_the_flow(stack):
    _m, _g, direct = stack
    key = {"X-Internal-Api-Key": "v2-internal-test-key"}
    snap = direct["catalog"].get("/internal/v1/replica/catalog.enterprises", headers=key).json()
    from events_service.startup import REPLICAS

    assert REPLICAS.local_hash("catalog.enterprises") == snap["hash"]
    assert REPLICAS.sync_once() == {"catalog.enterprises": "current", "catalog.enterprise_locations": "current"}


def test_replicas_are_read_only_in_events(stack):
    import pytest

    from events_service.db.database import SessionLocal
    from events_service.models.enterprise_model import Enterprise
    from mskit.replication import ReplicaWriteError

    with SessionLocal() as db:
        ent = db.query(Enterprise).first()
        ent.business_short_name = "changed in the wrong service"
        with pytest.raises(ReplicaWriteError):
            db.commit()
