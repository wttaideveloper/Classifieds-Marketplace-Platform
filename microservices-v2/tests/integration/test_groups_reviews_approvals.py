"""The last cross-service parts vs. the monolith:

* D5 - group chats read events/registrations and trainings/enrolments from communication's read-only replicas:
  an attendee who registers in events appears in the event's attendee list in communication;
* D6 - the review moderation queue is gathered from catalog, learning and events and merged by moderation;
  approvals under /admin are answered by events and learning.
"""
import json

from flows import run_flow
from test_catalog_events import FORM as EVENT_FORM
from test_learning_on_catalog import FORM as TRAINING_FORM

TENANT = "7a7a7a7a-0000-4000-8000-000000000001"

STEPS = [
    {"as": "admin", "method": "POST", "path": "/api/v1/enterprises/", "expect": 201,
     "json": {"business_short_name": "Asha Community", "business_email": "community@example.com", "tenant_id": TENANT, "status": "active"}, "save": {"ent": "id"}},
    {"as": "super_admin", "method": "POST", "path": "/api/v1/admin/event-form-configurations/", "json": EVENT_FORM, "save": {"ecfg": "id"}},
    {"as": "super_admin", "method": "POST", "path": "/api/v1/admin/event-form-configurations/{ecfg}/publish"},
    {"as": "super_admin", "method": "POST", "path": "/api/v1/admin/event-form-configurations/{ecfg}/activate"},
    {"as": "admin", "method": "POST", "path": "/api/v1/events/", "expect": 201,
     "json": {"title": "Diabetes Support Meetup", "description": "Monthly meetup", "pricing_type": "free", "category": "health",
              "start_date": "2027-07-01T17:00:00", "end_date": "2027-07-01T19:00:00", "enterprise_id": "{ent}", "delivery_mode": "in_person",
              "tenant_id": TENANT}, "save": {"ev": "id"}},
    # approvals are answered by events now (same paths)
    {"as": "admin", "method": "GET", "path": "/api/v1/admin/events/pending"},
    {"as": "super_admin", "method": "GET", "path": "/api/v1/admin/events/pending"},
    {"as": "super_admin", "method": "POST", "path": "/api/v1/admin/events/{ev}/approve"},
    {"as": "super_admin", "method": "POST", "path": "/api/v1/admin/events/{ev}/publish"},
    {"as": "super_admin", "method": "GET", "path": "/api/v1/admin/event-audits/{ev}"},
    {"as": "super_admin", "method": "GET", "path": "/api/v1/admin/event-categories"},
    # a registration in events, then the group chat view of the event in communication (replicas)
    {"as": "customer", "method": "POST", "path": "/api/v1/events/{ev}/registrations", "json": {"participant_name": "Meera S", "participant_email": "customer@example.invalid"}},
    {"as": "admin", "method": "GET", "path": "/api/v1/groups/events"},
    {"as": "admin", "method": "GET", "path": "/api/v1/groups/events/{ev}/attendees"},
    {"as": "admin", "method": "GET", "path": "/api/v1/groups/events/{ev}/groups"},
    {"as": "customer", "method": "GET", "path": "/api/v1/groups/events/{ev}/attendees"},
    # trainings: approvals by learning, learners list by communication
    {"as": "super_admin", "method": "POST", "path": "/api/v1/admin/training-form-configurations/", "json": TRAINING_FORM, "save": {"tcfg": "id"}},
    {"as": "super_admin", "method": "POST", "path": "/api/v1/admin/training-form-configurations/{tcfg}/publish"},
    {"as": "super_admin", "method": "POST", "path": "/api/v1/admin/training-form-configurations/{tcfg}/activate"},
    {"as": "admin", "method": "POST", "path": "/api/v1/trainings/", "expect": 201,
     "json": {"enterprise_id": "{ent}", "title": "Foot Care Basics", "category": "health", "tenant_id": TENANT}, "save": {"tr": "id"}},
    {"as": "super_admin", "method": "GET", "path": "/api/v1/admin/trainings/pending"},
    {"as": "super_admin", "method": "GET", "path": "/api/v1/admin/trainings/{tr}"},
    {"as": "super_admin", "method": "POST", "path": "/api/v1/admin/trainings/{tr}/approve"},
    {"as": "admin", "method": "GET", "path": "/api/v1/groups/trainings"},
    {"as": "admin", "method": "GET", "path": "/api/v1/groups/trainings/{tr}/learners"},
    # reviews in catalog, then the moderation queue across modules
    {"as": "admin", "method": "POST", "path": "/api/v1/products/", "expect": 201,
     "json": {"enterprise_id": "{ent}", "product_name": "Glucometer", "category": "devices", "price": 999.0}, "save": {"p": "id"}},
    {"as": "customer", "method": "POST", "path": "/api/v1/products/{p}/reviews", "json": {"rating": 4, "comment": "Accurate"}, "save": {"rev": "id"}},
    {"as": "super_admin", "method": "GET", "path": "/api/v1/reviews/manage"},
    {"as": "super_admin", "method": "GET", "path": "/api/v1/reviews/manage", "params": {"module": "product", "status": "pending"}},
    {"as": "admin", "method": "GET", "path": "/api/v1/reviews/manage", "params": {"page": 1, "page_size": 1}},
    {"as": "customer", "method": "GET", "path": "/api/v1/reviews/manage"},
    {"as": "super_admin", "method": "GET", "path": "/api/v1/reviews/manage", "params": {"module": "podcast"}},
    {"as": "admin", "method": "PATCH", "path": "/api/v1/reviews/product/{rev}/moderate", "json": {"action": "approved"}},
    {"as": "super_admin", "method": "PATCH", "path": "/api/v1/reviews/product/{rev}/moderate", "json": {"action": "approved"}},
    {"as": "super_admin", "method": "PATCH", "path": "/api/v1/reviews/product/{rev}/moderate", "json": {"action": "rejected", "reason": "not about the product"}},
    {"as": "super_admin", "method": "GET", "path": "/api/v1/reviews/audit"},
    {"as": "super_admin", "method": "DELETE", "path": "/api/v1/reviews/product/{rev}", "json": {"reason": "spam"}},
    {"as": "super_admin", "method": "GET", "path": "/api/v1/reviews/manage", "params": {"module": "product"}},
    {"as": "super_admin", "method": "GET", "path": "/api/v1/admin/notifications/diagnostics", "params": {"event_id": "{ev}"}},
]


def test_groups_reviews_and_approvals_behave_like_the_monolith(stack):
    monolith, gateway, _direct = stack
    diffs, log, _ = run_flow(monolith, gateway, STEPS)
    assert not diffs, json.dumps(diffs, indent=1)
    statuses = [row[3] for row in log]
    assert statuses.count(200) + statuses.count(201) >= 20, log
