"""Learning through the gateway on catalog's replicated enterprises vs. the monolith: trainings (also as /courses),
publication, search, enrolment, listing, deletion; programs; the learning replica stays identical to catalog."""
import json

from flows import run_flow

TENANT = "7a7a7a7a-0000-4000-8000-000000000001"

FORM = {"name": "Default training form", "scope": "global", "sections": [{
    "stable_key": "section_main", "label": "Training", "position": 1, "is_enabled": True,
    "fields": [{"source": "core", "core_key": "title", "label": "Title", "renderer": "text", "position": 1, "required": True, "is_enabled": True},
               {"source": "core", "core_key": "category", "label": "Category", "renderer": "select", "position": 2, "required": True, "is_enabled": True}]}]}

STEPS = [
    {"as": "admin", "method": "POST", "path": "/api/v1/enterprises/", "expect": 201,
     "json": {"business_short_name": "Asha Academy", "business_email": "academy@example.com", "tenant_id": TENANT, "status": "active", "city": "Chennai"}, "save": {"ent": "id"}},
    {"as": "super_admin", "method": "POST", "path": "/api/v1/admin/training-form-configurations/", "json": FORM, "save": {"cfg": "id"}},
    {"as": "super_admin", "method": "POST", "path": "/api/v1/admin/training-form-configurations/{cfg}/publish"},
    {"as": "super_admin", "method": "POST", "path": "/api/v1/admin/training-form-configurations/{cfg}/activate"},
    {"as": "admin", "method": "POST", "path": "/api/v1/trainings/", "expect": 201, "json": {"enterprise_id": "{ent}", "title": "First Aid Basics", "category": "health", "tenant_id": TENANT}, "save": {"tr": "id"}},
    {"as": "admin", "method": "POST", "path": "/api/v1/courses/", "json": {"enterprise_id": "{ent}", "title": "CPR Course", "category": "health", "tenant_id": TENANT}, "save": {"co": "id"}},
    {"as": "admin", "method": "POST", "path": "/api/v1/trainings/", "json": {"enterprise_id": "{ent}"}},
    {"as": "admin", "method": "GET", "path": "/api/v1/trainings/{tr}"},
    {"as": None, "method": "GET", "path": "/api/v1/trainings/{tr}"},
    {"as": "admin", "method": "PUT", "path": "/api/v1/trainings/{tr}", "json": {"title": "First Aid Basics (2027)"}},
    {"as": "admin", "method": "GET", "path": "/api/v1/trainings/", "params": {"page": 1, "page_size": 5}},
    {"as": None, "method": "GET", "path": "/api/v1/courses/", "params": {"category": "health"}},
    {"as": None, "method": "GET", "path": "/api/v1/search/trainings", "params": {"query": "aid"}},
    {"as": None, "method": "GET", "path": "/api/v1/search/programs", "params": {"query": "x"}},
    {"as": "admin", "method": "POST", "path": "/api/v1/programs/", "json": {"enterprise_id": "{ent}", "title": "Wellness Program", "category": "health", "tenant_id": TENANT}, "save": {"pr": "id"}},
    {"as": None, "method": "GET", "path": "/api/v1/programs/"},
    {"as": "customer", "method": "GET", "path": "/api/v1/me/trainings"},
    {"as": "admin", "method": "DELETE", "path": "/api/v1/courses/{co}"},
    {"as": "admin", "method": "GET", "path": "/api/v1/courses/{co}"},
    {"as": "admin", "method": "PUT", "path": "/api/v1/enterprises/{ent}", "json": {"business_short_name": "Asha Academy Online"}},
    {"as": "admin", "method": "GET", "path": "/api/v1/trainings/{tr}"},
]


def test_learning_on_replicated_catalog_data_behaves_like_the_monolith(stack):
    monolith, gateway, direct = stack
    diffs, log, sides = run_flow(monolith, gateway, STEPS)
    assert not diffs, json.dumps(diffs, indent=1)
    statuses = [row[3] for row in log]
    assert statuses.count(200) + statuses.count(201) >= 10, log


def test_learning_replica_matches_catalog(stack):
    from learning_service.startup import REPLICAS

    assert REPLICAS.sync_once() == {"catalog.enterprises": "current", "catalog.enterprise_locations": "current"}
