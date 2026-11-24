"""Catalog happy paths vs. the monolith + the internal API on real catalog data."""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

V2 = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(V2 / "tests" / "support"))

from catalog_service import main  # noqa: E402

TENANT = "7a7a7a7a-0000-4000-8000-000000000001"

STEPS = [
    {"as": "admin", "method": "POST", "path": "/api/v1/enterprises/", "expect": 201,
     "json": {"business_short_name": "Asha Clinic", "business_legal_name": "Asha Clinic Pvt Ltd", "business_email": "clinic@example.com", "tenant_id": TENANT, "city": "Bengaluru"},
     "save": {"ent": "id"}},
    {"as": "admin", "method": "POST", "path": "/api/v1/enterprises/", "expect": 201,
     "json": {"business_short_name": "Second Branch", "business_email": "second@example.com", "tenant_id": TENANT},
     "save": {"ent2": "id"}},
    {"as": None, "method": "GET", "path": "/api/v1/enterprises/", "params": {"page": 1, "page_size": 1}},
    {"as": "admin", "method": "GET", "path": "/api/v1/enterprises/{ent}"},
    {"as": "admin", "method": "PUT", "path": "/api/v1/enterprises/{ent}", "json": {"business_short_name": "Asha Clinic & Labs"}},
    {"as": "admin", "method": "POST", "path": "/api/v1/enterprises/{ent}/locations", "json": {"location_name": "MG Road", "city": "Bengaluru"}, "save": {"loc": "id"}},
    {"as": "admin", "method": "GET", "path": "/api/v1/enterprises/{ent}/locations"},
    {"as": "admin", "method": "POST", "path": "/api/v1/products/", "expect": 201,
     "json": {"enterprise_id": "{ent}", "product_name": "Vitamin D3", "category": "supplements", "price": 299.0, "stock_quantity": 10}, "save": {"prod": "id"}},
    {"as": "admin", "method": "POST", "path": "/api/v1/products/",
     "json": {"enterprise_id": "{ent}", "product_name": "Omega 3", "category": "supplements", "price": 499.5}, "save": {"prod2": "id"}},
    {"as": "admin", "method": "POST", "path": "/api/v1/products/", "json": {"enterprise_id": "{ent}", "product_name": "x", "category": "c", "price": "not-a-number"}},
    {"as": "customer", "method": "POST", "path": "/api/v1/products/", "json": {"enterprise_id": "{ent}", "product_name": "x", "category": "c", "price": 1}},
    {"as": None, "method": "GET", "path": "/api/v1/products/", "params": {"category": "supplements", "page": 1, "page_size": 1}},
    {"as": "customer", "method": "GET", "path": "/api/v1/products/{prod}"},
    {"as": "admin", "method": "PUT", "path": "/api/v1/products/{prod}", "json": {"price": 349.0}},
    {"as": "admin", "method": "POST", "path": "/api/v1/services/", "expect": 201,
     "json": {"enterprise_id": "{ent}", "service_name": "Physiotherapy", "category": "therapy", "duration_minutes": 45, "price": 800.0}, "save": {"svc": "id"}},
    {"as": None, "method": "GET", "path": "/api/v1/services/"},
    {"as": "provider", "method": "GET", "path": "/api/v1/services/{svc}"},
    {"as": None, "method": "GET", "path": "/api/v1/search/products", "params": {"query": "vitamin"}},
    {"as": None, "method": "GET", "path": "/api/v1/search/enterprises", "params": {"query": "asha"}},
    {"as": None, "method": "GET", "path": "/api/v1/search/services", "params": {"query": "physio"}},
    {"as": "customer", "method": "POST", "path": "/api/v1/products/{prod}/reviews", "json": {"rating": 5, "comment": "Works well"}, "save": {"rev": "id"}},
    {"as": "customer", "method": "POST", "path": "/api/v1/services/{svc}/reviews", "json": {"rating": 4, "comment": "Good"}},
    {"as": None, "method": "GET", "path": "/api/v1/products/{prod}/reviews"},
    {"as": "super_admin", "method": "GET", "path": "/api/v1/products/{prod}/reviews"},
    {"as": "admin", "method": "PATCH", "path": "/api/v1/products/{prod}/reviews/{rev}/moderate", "json": {"action": "approved"}},
    {"as": "super_admin", "method": "PATCH", "path": "/api/v1/products/{prod}/reviews/{rev}/moderate", "json": {"action": "approved"}, "expect": 200},
    {"as": "super_admin", "method": "PATCH", "path": "/api/v1/products/{prod}/reviews/{rev}/moderate", "json": {"action": "rejected", "reason": "spam"}},
    {"as": None, "method": "GET", "path": "/api/v1/products/{prod}/reviews"},
    {"as": "admin", "method": "DELETE", "path": "/api/v1/products/{prod2}"},
    {"as": "admin", "method": "GET", "path": "/api/v1/products/{prod2}"},
    {"as": "admin", "method": "DELETE", "path": "/api/v1/enterprises/{ent2}"},
]


@pytest.fixture(scope="module")
def clients():
    from app.main import app as monolith_app
    with TestClient(main.app) as svc:
        yield TestClient(monolith_app), svc


def test_catalog_flow_matches_monolith(clients, stubs):
    from flows import run_flow

    diffs, log, sides = run_flow(*clients, STEPS)
    assert not diffs, json.dumps(diffs, indent=1)
    statuses = [row[3] for row in log]
    assert statuses.count(201) + statuses.count(200) >= 18, log  # the flow really ran its happy path
    # side writes that left catalog
    assert any(c[2] == "/review-log" for c in stubs), "review moderation history must be sent to moderation"
    pytest.saved = sides[1]["saved"]


def test_internal_api_on_real_data(clients):
    _mono, svc = clients
    saved = pytest.saved
    key = {"X-Internal-Api-Key": "v2-internal-test-key"}
    assert svc.get(f"/internal/v1/enterprises/{saved['ent']}/tenant").status_code == 403  # key required
    assert svc.get(f"/internal/v1/enterprises/{saved['ent']}/tenant", headers=key).json() == {"tenant_id": TENANT}
    assert svc.get(f"/internal/v1/enterprises/{saved['ent2']}/tenant", headers=key).status_code == 404  # deleted
    oldest = svc.get(f"/internal/v1/tenants/{TENANT}/enterprise", headers=key).json()
    assert oldest["id"] == saved["ent"] and oldest["business_short_name"] == "Asha Clinic & Labs"
    assert svc.get(f"/internal/v1/tenants/{TENANT}/enterprise", params={"enterprise_id": saved["ent2"]}, headers=key).status_code == 404
    names = svc.post("/internal/v1/enterprises/business-names", json={"tenant_ids": [TENANT]}, headers=key).json()
    assert names == {TENANT: "Asha Clinic & Labs"}


def test_tenant_lookup_is_local_in_catalog():
    from mskit.auth import tenant_lookup
    assert tenant_lookup.tenant_id_for_enterprise(None, pytest.saved["ent"]) == TENANT
