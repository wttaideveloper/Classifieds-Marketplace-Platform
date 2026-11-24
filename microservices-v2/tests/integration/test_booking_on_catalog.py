"""Bookings on catalog's replicated services (design D4) vs. the monolith: a service created in catalog is visible
to booking at once (staff settings, slots, booking attempts, lists), and later changes reach it too."""
import json

from flows import run_flow

TENANT = "7a7a7a7a-0000-4000-8000-000000000001"

STEPS = [
    {"as": "admin", "method": "POST", "path": "/api/v1/enterprises/", "expect": 201,
     "json": {"business_short_name": "Asha Physio", "business_email": "physio@example.com", "tenant_id": TENANT, "status": "active"}, "save": {"ent": "id"}},
    {"as": "admin", "method": "POST", "path": "/api/v1/services/", "expect": 201,
     "json": {"enterprise_id": "{ent}", "service_name": "Knee Physio", "category": "therapy", "duration_minutes": 45, "price": 900.0,
              "tenant_id": TENANT}, "save": {"svc": "id"}},
    {"as": "admin", "method": "GET", "path": "/api/v1/services/{svc}/booking-settings", "expect": 200},
    {"as": "customer", "method": "GET", "path": "/api/v1/services/{svc}/booking-settings"},
    {"as": None, "method": "GET", "path": "/api/v1/services/{svc}/available-slots", "params": {"date": "2027-02-01"}},
    {"as": "customer", "method": "POST", "path": "/api/v1/service-bookings", "json": {"service_id": "{svc}", "starts_at": "2027-02-01T10:00:00+05:30"}},
    {"as": "customer", "method": "POST", "path": "/api/v1/service-bookings", "json": {"service_id": "{svc}", "starts_at": "2027-02-01T10:00:00"}},
    {"as": "customer", "method": "GET", "path": "/api/v1/users/me/service-bookings"},
    {"as": "admin", "method": "GET", "path": "/api/v1/enterprises/{ent}/service-bookings"},
    {"as": "admin", "method": "PUT", "path": "/api/v1/services/{svc}", "json": {"service_name": "Knee & Hip Physio", "duration_minutes": 60}},
    {"as": "admin", "method": "GET", "path": "/api/v1/services/{svc}/booking-settings"},
    {"as": "admin", "method": "DELETE", "path": "/api/v1/services/{svc}"},
    {"as": "admin", "method": "GET", "path": "/api/v1/services/{svc}/booking-settings"},
]


def test_booking_on_replicated_services_behaves_like_the_monolith(stack):
    monolith, gateway, _direct = stack
    diffs, log, _ = run_flow(monolith, gateway, STEPS)
    assert not diffs, json.dumps(diffs, indent=1)
