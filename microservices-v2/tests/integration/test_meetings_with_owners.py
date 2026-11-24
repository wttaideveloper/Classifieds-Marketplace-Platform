"""Meetings with the events and learning services as owners (design D7) vs. the monolith.

Events/trainings change -> the hooks run in meetings on snapshots; meetings' own endpoints authorize staff through the
owners, read event sessions and training live sessions there, and answer the customer views from their registrations.
"""
import json

from flows import run_flow
from test_catalog_events import FORM as EVENT_FORM
from test_learning_on_catalog import FORM as TRAINING_FORM

TENANT = "7a7a7a7a-0000-4000-8000-000000000001"

STEPS = [
    {"as": "admin", "method": "POST", "path": "/api/v1/enterprises/", "expect": 201,
     "json": {"business_short_name": "Asha Online", "business_email": "online@example.com", "tenant_id": TENANT, "status": "active"}, "save": {"ent": "id"}},
    {"as": "super_admin", "method": "POST", "path": "/api/v1/admin/event-form-configurations/", "json": EVENT_FORM, "save": {"ecfg": "id"}},
    {"as": "super_admin", "method": "POST", "path": "/api/v1/admin/event-form-configurations/{ecfg}/publish"},
    {"as": "super_admin", "method": "POST", "path": "/api/v1/admin/event-form-configurations/{ecfg}/activate"},
    {"as": "admin", "method": "POST", "path": "/api/v1/events/", "expect": 201,
     "json": {"title": "Online Yoga", "description": "Live class", "pricing_type": "free", "category": "wellness",
              "start_date": "2027-06-01T07:00:00", "end_date": "2027-06-01T08:00:00", "enterprise_id": "{ent}",
              "delivery_mode": "online", "meeting_provider": "zoom", "tenant_id": TENANT}, "save": {"ev": "id"}},
    {"as": "super_admin", "method": "PATCH", "path": "/api/v1/events/{ev}/status", "json": {"status": "published"}},
    {"as": "admin", "method": "GET", "path": "/api/v1/meetings/events/{ev}"},
    {"as": "super_admin", "method": "GET", "path": "/api/v1/meetings/events/{ev}"},
    {"as": "super_admin", "method": "POST", "path": "/api/v1/meetings/events/{ev}/generate"},          # read-only for a Super Admin
    {"as": "customer", "method": "GET", "path": "/api/v1/meetings/events/{ev}"},                     # not staff
    {"as": "admin", "method": "GET", "path": "/api/v1/meetings/events/{ev}/sessions"},
    {"as": "admin", "method": "PUT", "path": "/api/v1/meetings/events/{ev}", "json": {"manual_link": "https://example.com/my-room"}},
    {"as": None, "method": "GET", "path": "/api/v1/events/{ev}"},                                     # the event's own view of the link
    {"as": "customer", "method": "GET", "path": "/api/v1/meetings/my/events/{ev}"},                  # not registered: 404
    {"as": "customer", "method": "POST", "path": "/api/v1/events/{ev}/registrations", "json": {"participant_name": "Ravi K", "participant_email": "customer@example.invalid"}},
    {"as": "customer", "method": "GET", "path": "/api/v1/meetings/my/events/{ev}"},
    {"as": "admin", "method": "PUT", "path": "/api/v1/events/{ev}", "json": {"title": "Online Yoga (moved)", "start_date": "2027-06-02T07:00:00", "end_date": "2027-06-02T08:00:00"}},
    {"as": "admin", "method": "GET", "path": "/api/v1/meetings/events/{ev}"},
    {"as": "super_admin", "method": "POST", "path": "/api/v1/admin/training-form-configurations/", "json": TRAINING_FORM, "save": {"tcfg": "id"}},
    {"as": "super_admin", "method": "POST", "path": "/api/v1/admin/training-form-configurations/{tcfg}/publish"},
    {"as": "super_admin", "method": "POST", "path": "/api/v1/admin/training-form-configurations/{tcfg}/activate"},
    {"as": "admin", "method": "POST", "path": "/api/v1/trainings/", "expect": 201,
     "json": {"enterprise_id": "{ent}", "title": "Live Nutrition", "category": "health", "tenant_id": TENANT}, "save": {"tr": "id"}},
    {"as": "admin", "method": "GET", "path": "/api/v1/meetings/trainings/{tr}/sessions"},
    {"as": "admin", "method": "GET", "path": "/api/v1/meetings/trainings/{tr}/sessions/0f0f0f0f-0000-4000-8000-00000000abcd"},
    {"as": "customer", "method": "GET", "path": "/api/v1/meetings/my/trainings/{tr}"},
    {"as": "admin", "method": "DELETE", "path": "/api/v1/events/{ev}"},
    {"as": "admin", "method": "GET", "path": "/api/v1/meetings/events/{ev}"},
]


def test_meetings_with_events_and_learning_behave_like_the_monolith(stack):
    monolith, gateway, _direct = stack
    diffs, log, _ = run_flow(monolith, gateway, STEPS)
    assert not diffs, json.dumps(diffs, indent=1)
    statuses = [row[3] for row in log]
    assert statuses.count(200) + statuses.count(201) >= 12, log
