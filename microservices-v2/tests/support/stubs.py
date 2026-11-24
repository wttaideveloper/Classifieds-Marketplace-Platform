"""Stand-ins for the OTHER services in single-service tests, at the transport level (mskit.internal).

Each fake endpoint answers like the owning service would with an empty database - i.e. what the
monolith's own code sees in its (empty) tables during the side-by-side comparison. Every call is
recorded so tests can assert on the cross-service traffic.
"""
import json
import re
from uuid import uuid4

import httpx

CALLS: list[tuple] = []


def _json(status, payload):
    return httpx.Response(status, content=json.dumps(payload).encode(), headers={"content-type": "application/json"})


ROUTES = [
    # notifications
    ("POST", r"^/notifications/automatic$", lambda body: _json(201, {"id": str(uuid4()), "status": "sent"} if body.get("user_ids") else {"id": None, "status": None})),
    ("POST", r"^/notification-dedupe/claim$", lambda body: _json(200, {"claimed": True})),
    ("POST", r"^/notification-dedupe/release$", lambda body: _json(200, {"released": True})),
    # communication
    ("GET", r"^/users/[^/]+/delivery-profile$", lambda body: _json(200, {"push_enabled": True, "tokens": [], "email": None})),
    # catalog
    ("GET", r"^/tenants/[^/]+/enterprise$", lambda body: _json(404, {"detail": "No enterprise"})),
    ("POST", r"^/enterprises/business-names$", lambda body: _json(200, {})),
    ("GET", r"^/enterprises/[^/]+/tenant$", lambda body: _json(404, {"detail": "Not found"})),
    # catalog stock (D1): with no products every line is "not available", the monolith's first check
    ("POST", r"^/stock/reservations$", lambda body: _json(400, {"detail": f"Product {body['items'][0]['product_id']} is not available"})),
    ("DELETE", r"^/stock/reservations/[^/]+$", lambda body: _json(200, {"status": "released"})),
    ("POST", r"^/stock/reservations/[^/]+/confirm$", lambda body: _json(200, {"status": "confirmed"})),
    ("POST", r"^/stock/releases$", lambda body: _json(200, {"restocked": True})),
    # events (D2)
    ("GET", r"^/event-orders/[^/]+$", lambda body: _json(404, {"detail": "Order not found"})),
    ("POST", r"^/event-orders/[^/]+/(payment-confirmed|refunded)$", lambda body: _json(200, {"applied": True})),
    # commerce
    ("GET", r"^/users/[^/]+/confirmed-purchases/[^/]+$", lambda body: _json(200, {"purchased": False})),
    # meetings (no provider credentials configured, no meeting rows: what the monolith sees in tests)
    ("GET", r"^/providers/placeholders-allowed$", lambda body: _json(200, {"allowed": True})),
    ("POST", r"^/records/is-generated-link$", lambda body: _json(200, {"generated": False})),
    ("POST", r"^/hooks/(events|trainings)/[a-z]+$", lambda body: _json(200, {"writes": []})),
    # events / learning as owners of what meetings manages (D7), with no events or trainings
    ("POST", r"^/events/[^/]+/staff-access$", lambda body: _json(404, {"detail": "Event not found"})),
    ("POST", r"^/trainings/[^/]+/staff-access$", lambda body: _json(403, {"detail": "Only the business that owns the training can manage its meetings"})
        if (body.get("user") or {}).get("role") not in ("admin", "provider", "super_admin") else _json(404, {"detail": "Training not found"})),
    ("GET", r"^/events/[^/]+/(public-snapshot|snapshot)$", lambda body: _json(404, {"detail": "Event not found"})),
    ("GET", r"^/trainings/[^/]+/snapshot$", lambda body: _json(404, {"detail": "Training not found"})),
    ("GET", r"^/trainings/[^/]+/live-sessions$", lambda body: _json(200, [])),
    ("GET", r"^/trainings/[^/]+/live-sessions/[^/]+$", lambda body: _json(404, {"detail": "Session not found"})),
    ("GET", r"^/live-sessions/[^/]+$", lambda body: _json(404, {"detail": "Session not found"})),
    ("GET", r"^/(events|trainings)/[^/]+/participant-emails$", lambda body: _json(200, {"emails": []})),
    ("GET", r"^/events/[^/]+/registrations/exists$", lambda body: _json(200, {"registered": False})),
    ("GET", r"^/trainings/[^/]+/enrolments/active$", lambda body: _json(200, {"enrolment": None})),
    # review owners (D6) with no reviews: empty slices, nothing to moderate or delete
    ("POST", r"^/reviews/moderation-slice$", lambda body: _json(200, {"counts": {"pending": 0, "approved": 0, "rejected": 0}, "total": 0, "rows": []})),
    ("POST", r"^/reviews/[a-z]+/[^/]+/(moderate|delete)$", lambda body: _json(404, {"detail": "Review not found"})),
    ("GET", r"^/notifications/recorded/[^/]+$", lambda body: _json(200, [])),
    # communication's calls (D5, D8): identity fills, names, platform inbox read-sync - empty owners
    ("POST", r"^/(events|trainings)/[^/]+/(registrations|enrolments)/resolve-user-ids$", lambda body: _json(200, {})),
    ("POST", r"^/names$", lambda body: _json(200, {"sources": []})),
    ("POST", r"^/notifications/mark-read-by-conversation$", lambda body: _json(200, {"marked": 0})),
    # moderation
    ("POST", r"^/review-log$", lambda body: _json(201, {"recorded": True})),
]

# Endpoints whose empty-domain answer depends on the request are added by the tests that need them.
EXTRA: list = []


def fake_call(service, method, path, *, json=None, params=None, timeout=None, expect=(200, 201, 202, 204, 404), headers=None):
    from mskit.internal import ServiceError

    CALLS.append((service, method.upper(), path, json))
    for m, pattern, handler in EXTRA + ROUTES:
        if m == method.upper() and re.match(pattern, path):
            response = handler(json or {})
            if response.status_code not in expect:
                raise ServiceError(response.status_code, response.text)
            return response
    raise AssertionError(f"no stub for {service} {method} {path}")


def install(monkeypatch):
    from mskit import clients, internal
    from mskit.auth import tenant_lookup

    CALLS.clear()
    monkeypatch.setattr(internal, "call", fake_call)
    monkeypatch.setattr(clients, "call", fake_call)
    monkeypatch.setattr(tenant_lookup, "_remote", lambda enterprise_id: None)
    return CALLS
