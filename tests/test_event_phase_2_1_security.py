"""
Event Management Phase 2.1 — tenant isolation / visibility / IDOR regression tests.

These run against a real (in-memory SQLite) database via tests/event_sql_support.py, so the assertions
cover the actual SQL and ownership logic, not canned mock query chains.

Covers audit findings: missing event ownership (every staff route), tenant_id mass assignment, public
draft/pending/rejected exposure, session meeting-link leakage, customer-level IDORs (cancel/refund),
template tenant fail-open, reports/summary cross-tenant aggregation, auto-complete scope, and the
Event admin routes' Enterprise-Admin fallback.

Run:
    pytest tests/test_event_phase_2_1_security.py -v
"""
import copy
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from event_sql_support import (
    API,
    EventAudit,
    EventFeedback,
    EventOrder,
    EventRegistration,
    EventTemplate,
    EventWaitlist,
    Event,
    client_for,
    customer_user,
    make_enterprise,
    make_event,
    make_order,
    make_registration,
    make_session,
    make_waitlist,
    paid_ticket,
    reset_overrides,
    silence_side_effects,
    staff_user,
    super_admin_user,
)


SESSION_LINK = "https://meet.example.com/secret-room"


@pytest.fixture
def world(monkeypatch):
    """Tenant B owns a rich event; tenant A is the attacker's tenant."""
    silence_side_effects(monkeypatch)
    db = make_session()
    tenant_a, tenant_b = uuid4(), uuid4()
    ent_a, ent_b = make_enterprise(db, tenant_a, "A"), make_enterprise(db, tenant_b, "B")
    ticket = paid_ticket()
    session_date = (datetime.utcnow() + timedelta(days=10)).date().isoformat()
    event_b = make_event(
        db, tenant_b, ent_b,
        pricing_type="paid", price="500", ticket_types=[ticket], capacity="50",
        sessions=[{"id": "sess-1", "session_date": session_date, "title": "Keynote", "meeting_link": SESSION_LINK}],
        delivery_mode="online", meeting_link="https://meet.example.com/main-room",
    )
    reg_b = make_registration(db, event_b, "victim@example.com", ticket_type_id=ticket["id"])
    order_b = make_order(db, event_b, "victim@example.com", ticket_type_id=ticket["id"])
    wait_b = make_waitlist(db, event_b, "waiter@example.com")
    review_b = EventFeedback(event_id=event_b.id, participant_email="victim@example.com", is_review=True, rating="5")
    db.add(review_b)
    db.commit()
    world = SimpleNamespace(
        db=db, tenant_a=tenant_a, tenant_b=tenant_b, ent_a=ent_a, ent_b=ent_b,
        event=event_b, reg=reg_b, order=order_b, wait=wait_b, review=review_b, ticket=ticket,
        session_date=session_date,
        attacker=staff_user(tenant_a, "admin"), owner=staff_user(tenant_b, "admin"),
    )
    yield world
    reset_overrides()
    db.close()


def snapshot(db):
    """Everything an attacker could have changed on the victim's data."""
    db.expire_all()
    return {
        "events": [(str(e.id), e.title, e.status, e.is_deleted, str(e.tenant_id), json.dumps(e.sessions, sort_keys=True), e.last_admin_notes)
                   for e in db.query(Event).order_by(Event.created_at, Event.id).all()],
        "regs": sorted((str(r.id), r.status, str(r.checked_in_at), str(r.checked_out_at)) for r in db.query(EventRegistration).all()),
        "orders": sorted((str(o.id), o.status, o.payment_status) for o in db.query(EventOrder).all()),
        "waitlist": sorted((str(w.id), w.status) for w in db.query(EventWaitlist).all()),
        "reviews": sorted((str(f.id), f.moderation_status) for f in db.query(EventFeedback).all()),
        "audits": db.query(EventAudit).count(),
    }


def fmt(obj, ids):
    """Fill {placeholders} anywhere inside a JSON-able structure."""
    if isinstance(obj, str):
        return obj.format(**ids)
    if isinstance(obj, dict):
        return {k: fmt(v, ids) for k, v in obj.items()}
    if isinstance(obj, list):
        return [fmt(v, ids) for v in obj]
    return obj


# Every event-scoped staff route (method, path, json body). {e}=event {r}=registration {o}=order ...
STAFF_ROUTES = [
    ("PUT", "/{e}", {"title": "HACKED"}),
    ("DELETE", "/{e}", None),
    ("POST", "/{e}/duplicate", None),
    ("PATCH", "/{e}/status", {"status": "cancelled"}),
    ("POST", "/{e}/unpublish", None),
    ("POST", "/{e}/archive", None),
    ("POST", "/{e}/resubmit", None),
    ("GET", "/{e}/admin-notes", None),
    ("GET", "/{e}/form-configuration", None),
    ("GET", "/{e}/registrations", None),
    ("GET", "/{e}/registrations/export", None),
    ("GET", "/{e}/registrations/{r}", None),  # Phase 2.3
    ("GET", "/{e}/attendees", None),  # Phase 2.3
    ("GET", "/{e}/dashboard", None),  # Phase 2.3
    ("GET", "/{e}/fulfilment", None),  # Phase 2.8
    ("POST", "/{e}/sessions/sess-1/check-in", {"registration_id": "{r}"}),  # Phase 2.4
    ("POST", "/{e}/sessions/sess-1/uncheck-in", {"registration_id": "{r}"}),  # Phase 2.4
    ("POST", "/{e}/sessions/sess-1/check-out", {"registration_id": "{r}"}),  # Phase 2.4
    ("POST", "/{e}/sessions/sess-1/batch-check-in", {"participants": [{"registration_id": "{r}"}]}),  # Phase 2.4
    ("POST", "/{e}/walk-in", {"participant_name": "Intruder", "participant_email": "intruder@example.com"}),  # Phase 2.5
    ("PATCH", "/{e}/registrations/{r}/meals", {"meal_selections": []}),  # Phase 2.6
    ("PATCH", "/{e}/registrations/{r}/accommodation", {"accommodation_selections": []}),  # Phase 2.7
    ("GET", "/{e}/orders", None),
    ("POST", "/{e}/orders/{o}/refund", {}),
    ("PATCH", "/{e}/orders/{o}/status", {"status": "cancelled"}),
    ("POST", "/{e}/orders/{o}/refund/approve", {"action": "approve"}),
    ("GET", "/{e}/waitlist", None),
    ("DELETE", "/{e}/waitlist/{w}", None),
    ("POST", "/{e}/sessions", {"session_date": "{d}", "title": "Injected"}),
    ("PUT", "/{e}/sessions/sess-1", {"title": "Injected"}),
    ("DELETE", "/{e}/sessions/sess-1", None),
    ("POST", "/{e}/check-in", {"registration_id": "{r}"}),
    ("POST", "/{e}/uncheck-in", {"registration_id": "{r}"}),
    ("POST", "/{e}/check-out", {"registration_id": "{r}"}),
    ("POST", "/{e}/validate-qr", {"qr_code": "{qr}"}),
    ("GET", "/{e}/batch-check-in", None),
    ("POST", "/{e}/batch-check-in", {"participants": [{"registration_id": "{r}"}]}),
    ("GET", "/{e}/attendance", None),
    ("POST", "/{e}/announcements", {"message": "phishing"}),
    ("POST", "/{e}/remind", None),
    ("GET", "/{e}/feedback", None),
    ("PATCH", "/{e}/reviews/{f}/moderate", {"action": "rejected"}),
    ("GET", "/{e}/reports", None),
    ("GET", "/{e}/registrations/{r}/qr", None),
    ("GET", "/{e}/meeting-link", None),
    ("GET", "/{e}/sessions/sess-1/meeting-link", None),
    ("DELETE", "/{e}/registrations/{r}", None),
    ("POST", "/{e}/registrations/{r}/refund", {}),
]


def ids_for(w):
    return {"e": str(w.event.id), "r": str(w.reg.id), "o": str(w.order.id), "w": str(w.wait.id),
            "f": str(w.review.id), "d": w.session_date, "qr": w.reg.qr_code}


def call(client, method, path, body, ids):
    url = API + path.format(**ids)
    payload = fmt(copy.deepcopy(body), ids) if body is not None else None
    return client.request(method, url, json=payload)


# ===========================================================================
# 1. Event ownership — every staff route
# ===========================================================================


class TestCrossTenantStaffIsBlocked:
    """A staff member of tenant A must get 403 on EVERY route of tenant B's event, and change nothing."""

    @pytest.mark.parametrize("method,path,body", STAFF_ROUTES, ids=[f"{m} {p}" for m, p, _ in STAFF_ROUTES])
    @pytest.mark.parametrize("role", ["admin", "provider"])
    def test_route_403_and_no_side_effects(self, world, method, path, body, role):
        attacker = staff_user(world.tenant_a, role)
        before = snapshot(world.db)
        resp = call(client_for(world.db, attacker), method, path, body, ids_for(world))
        assert resp.status_code == 403, f"{method} {path} as tenant-A {role}: {resp.status_code} {resp.text}"
        assert snapshot(world.db) == before

    def test_route_matrix_covers_every_staff_dependency(self):
        """Guard against adding a staff route to event.py without adding it to this matrix."""
        from app.api.v1.endpoints import event as event_module

        guarded = set()
        for route in event_module.router.routes:
            for dep in route.dependant.dependencies:
                if getattr(dep.call, "__qualname__", "").startswith("_event_manager"):
                    for method in route.methods:
                        guarded.add((method, route.path))
        covered = set()
        for method, path, _ in STAFF_ROUTES:
            covered.add((method, path.replace("{e}", "{event_id}").replace("{o}", "{order_id}")
                         .replace("{w}", "{entry_id}").replace("{f}", "{review_id}").replace("{r}", "{reg_id}")))
        # Routes that share the same URL shape are matched by (method, path-with-placeholders).
        normalized_guarded = {(m, p) for m, p in guarded}
        missing = {(m, p) for (m, p) in normalized_guarded if (m, p) not in covered and not (m == "PUT" and p == "/{event_id}/sessions/{session_id}")}
        # session path uses a literal id in the matrix; tolerate that one shape.
        missing = {(m, p) for (m, p) in missing if "{session_id}" not in p}
        assert not missing, f"staff routes missing from the cross-tenant matrix: {sorted(missing)}"

    def test_same_tenant_staff_reaches_the_handlers(self, world):
        owner = world.owner
        client = client_for(world.db, owner)
        ids = ids_for(world)
        assert client.get(f"{API}/{ids['e']}/registrations").status_code == 200
        assert client.get(f"{API}/{ids['e']}/orders").status_code == 200
        assert client.get(f"{API}/{ids['e']}/waitlist").status_code == 200
        assert client.get(f"{API}/{ids['e']}/attendance").status_code == 200
        assert client.get(f"{API}/{ids['e']}/reports").status_code == 200
        assert client.post(f"{API}/{ids['e']}/validate-qr", json={"qr_code": world.reg.qr_code}).status_code == 200

    def test_customer_role_is_still_rejected_before_ownership(self, world):
        client = client_for(world.db, customer_user("victim@example.com"))
        assert client.get(f"{API}/{world.event.id}/registrations").status_code == 403

    def test_unauthenticated_is_401(self, world):
        assert client_for(world.db, None).get(f"{API}/{world.event.id}/registrations").status_code == 401

    def test_missing_event_is_404_not_403(self, world):
        resp = client_for(world.db, world.owner).get(f"{API}/{uuid4()}/registrations")
        assert resp.status_code == 404

    def test_staff_without_resolvable_tenant_is_denied_not_allowed(self, world):
        """WebAuth sessions may lack the tenant claim: that must fail closed, never open."""
        no_claim = {"id": str(uuid4()), "role": "admin", "email": "noclaim@example.com"}
        resp = client_for(world.db, no_claim).get(f"{API}/{world.event.id}/registrations")
        assert resp.status_code == 403

    def test_tenant_resolved_from_the_database_when_claim_is_absent(self, world):
        """A session with only an enterprise_id claim resolves its tenant from the enterprise row."""
        via_enterprise = {"id": str(uuid4()), "role": "admin", "email": "e@example.com", "enterprise_id": str(world.ent_b.id)}
        assert client_for(world.db, via_enterprise).get(f"{API}/{world.event.id}/registrations").status_code == 200
        foreign = {"id": str(uuid4()), "role": "admin", "email": "e2@example.com", "enterprise_id": str(world.ent_a.id)}
        assert client_for(world.db, foreign).get(f"{API}/{world.event.id}/registrations").status_code == 403

    def test_legacy_event_without_tenant_id_is_owned_through_its_enterprise(self, world):
        legacy = make_event(world.db, None, world.ent_b, title="Legacy")
        assert legacy.tenant_id is None
        assert client_for(world.db, world.owner).get(f"{API}/{legacy.id}/registrations").status_code == 200
        assert client_for(world.db, world.attacker).get(f"{API}/{legacy.id}/registrations").status_code == 403

    def test_event_with_no_resolvable_owner_is_platform_only(self, world):
        orphan = make_event(world.db, None, None, title="Orphan")
        assert client_for(world.db, world.owner).get(f"{API}/{orphan.id}/registrations").status_code == 403

    def test_admin_is_tenant_scoped_not_platform_wide(self, world):
        """Role 'admin' (tenant owner) must never act as a cross-tenant super user."""
        admin_a = staff_user(world.tenant_a, "admin")
        assert client_for(world.db, admin_a).put(f"{API}/{world.event.id}", json={"title": "x"}).status_code == 403

    def test_active_platform_super_admin_status_change_crosses_tenants(self, world):
        """The Platform Super Admin app approves other tenants' events through PATCH /status."""
        pending = make_event(world.db, world.tenant_b, world.ent_b, status="pending_approval", title="Pending")
        resp = client_for(world.db, super_admin_user()).patch(f"{API}/{pending.id}/status", json={"status": "approved"})
        assert resp.status_code == 200, resp.text
        world.db.expire_all()
        assert world.db.get(Event, pending.id).status == "approved"

    def test_inactive_super_admin_is_not_platform(self, world):
        inactive = {**super_admin_user(), "status": "disabled"}
        resp = client_for(world.db, inactive).patch(f"{API}/{world.event.id}/status", json={"status": "cancelled"})
        assert resp.status_code == 403


# ===========================================================================
# 2. tenant_id mass assignment
# ===========================================================================


class TestTenantIdIsImmutable:
    def test_put_cannot_move_event_to_another_tenant(self, world):
        own = make_event(world.db, world.tenant_a, world.ent_a, title="Mine")
        client = client_for(world.db, world.attacker)
        resp = client.put(f"{API}/{own.id}", json={"title": "Renamed", "tenant_id": str(world.tenant_b)})
        assert resp.status_code == 200, resp.text
        world.db.expire_all()
        refreshed = world.db.get(Event, own.id)
        assert refreshed.title == "Renamed"  # the legitimate part of the update still applied
        assert str(refreshed.tenant_id) == str(world.tenant_a)
        assert resp.json()["tenant_id"] == str(world.tenant_a)

    def test_event_update_schema_never_emits_tenant_id(self):
        from app.schemas.event_schema import EventUpdate

        assert "tenant_id" not in EventUpdate(title="x", tenant_id=uuid4()).to_model_data()

    def test_repository_update_ignores_protected_fields_even_from_non_schema_input(self, world):
        from app.repository.event_repo import update_event

        own = make_event(world.db, world.tenant_a, world.ent_a)
        payload = SimpleNamespace(model_dump=lambda exclude_unset=True: {"title": "T2", "tenant_id": world.tenant_b, "is_deleted": True, "id": uuid4()})
        original_id = own.id
        update_event(world.db, own, payload)
        world.db.expire_all()
        fresh = world.db.get(Event, original_id)
        assert fresh.title == "T2"
        assert str(fresh.tenant_id) == str(world.tenant_a)
        assert fresh.is_deleted is False


# ===========================================================================
# 3. Public / participant / staff visibility
# ===========================================================================


NON_PUBLIC = ["draft", "pending_approval", "rejected", "needs_revision", "suspended", "approved", "cancelled", "completed", "archived"]


class TestEventVisibility:
    @pytest.fixture
    def anonymous(self, world):
        return client_for(world.db, None)

    @pytest.mark.parametrize("status", NON_PUBLIC)
    def test_anonymous_detail_hides_non_published(self, world, anonymous, status):
        event = make_event(world.db, world.tenant_b, world.ent_b, status=status)
        assert anonymous.get(f"{API}/{event.id}").status_code == 404

    @pytest.mark.parametrize("status", NON_PUBLIC)
    def test_customer_detail_hides_non_published(self, world, status):
        event = make_event(world.db, world.tenant_b, world.ent_b, status=status)
        assert client_for(world.db, customer_user("stranger@example.com")).get(f"{API}/{event.id}").status_code == 404

    def test_published_is_public(self, world, anonymous):
        resp = anonymous.get(f"{API}/{world.event.id}")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Original title"

    def test_anonymous_list_and_search_return_only_published(self, world, anonymous):
        for status in NON_PUBLIC:
            make_event(world.db, world.tenant_b, world.ent_b, status=status, title=f"hidden-{status}")
        for url in (f"{API}/", f"{API}/?page_size=100", "/api/v1/search/events?page_size=100"):
            items = anonymous.get(url).json()["items"]
            assert items, url
            assert {i["status"] for i in items} == {"published"}, url

    def test_explicit_status_filter_cannot_reveal_hidden_events(self, world, anonymous):
        make_event(world.db, world.tenant_b, world.ent_b, status="draft", title="secret draft")
        for url in (f"{API}/?status=draft", f"{API}/?status=pending_approval", "/api/v1/search/events?status=draft"):
            assert anonymous.get(url).json()["items"] == [], url

    def test_mobile_query_status_published_is_unchanged(self, world):
        make_event(world.db, world.tenant_b, world.ent_b, status="draft", title="secret draft")
        resp = client_for(world.db, customer_user("mobile@example.com")).get(f"{API}/?status=published&page=1&page_size=50")
        assert resp.status_code == 200
        assert [i["title"] for i in resp.json()["items"]] == ["Original title"]

    def test_stale_or_invalid_token_is_treated_as_anonymous(self, world, monkeypatch):
        """Public endpoints never required a token; an expired one must not start failing them."""
        from app.core import dependencies

        reset_overrides()
        from app.db.database import get_db
        from app.main import app
        from fastapi.testclient import TestClient

        app.dependency_overrides[get_db] = lambda: world.db
        resp = TestClient(app, raise_server_exceptions=False).get(f"{API}/", headers={"Authorization": "Bearer not-a-real-token"})
        assert resp.status_code == 200
        assert {i["status"] for i in resp.json()["items"]} == {"published"}

    def test_registered_participant_can_open_own_cancelled_and_completed_events(self, world):
        me = customer_user("Mobile.User@Example.com")  # mixed case token email
        for status in ("cancelled", "completed", "archived"):
            event = make_event(world.db, world.tenant_b, world.ent_b, status=status, title=f"past-{status}")
            make_registration(world.db, event, "mobile.user@example.com", status="confirmed")
            resp = client_for(world.db, me).get(f"{API}/{event.id}")
            assert resp.status_code == 200, (status, resp.text)
        # ...but only THEIR events.
        other = make_event(world.db, world.tenant_b, world.ent_b, status="completed", title="not mine")
        assert client_for(world.db, me).get(f"{API}/{other.id}").status_code == 404

    def test_waitlisted_participant_can_open_the_event(self, world):
        event = make_event(world.db, world.tenant_b, world.ent_b, status="suspended")
        make_waitlist(world.db, event, "queue@example.com")
        assert client_for(world.db, customer_user("queue@example.com")).get(f"{API}/{event.id}").status_code == 200

    def test_my_registrations_lookup_is_case_insensitive(self, world):
        make_registration(world.db, world.event, "case.user@example.com")
        resp = client_for(world.db, customer_user("CASE.User@Example.com")).get(f"{API}/my/registrations")
        assert [r["event_id"] for r in resp.json()] == [str(world.event.id)]

    def test_owning_staff_see_their_non_public_events_others_do_not(self, world):
        draft = make_event(world.db, world.tenant_b, world.ent_b, status="draft", title="draft-b")
        assert client_for(world.db, world.owner).get(f"{API}/{draft.id}").status_code == 200
        titles = [i["title"] for i in client_for(world.db, world.owner).get(f"{API}/?page_size=100").json()["items"]]
        assert "draft-b" in titles
        assert client_for(world.db, world.attacker).get(f"{API}/{draft.id}").status_code == 404
        titles = [i["title"] for i in client_for(world.db, world.attacker).get(f"{API}/?page_size=100").json()["items"]]
        assert "draft-b" not in titles
        # the attacker still sees tenant B's PUBLISHED event (it is public)
        assert "Original title" in titles

    def test_legacy_event_without_tenant_id_is_visible_to_its_enterprise_staff_only(self, world):
        legacy = make_event(world.db, None, world.ent_b, status="draft", title="legacy-draft")
        owner_titles = [i["title"] for i in client_for(world.db, world.owner).get(f"{API}/?page_size=100").json()["items"]]
        other_titles = [i["title"] for i in client_for(world.db, world.attacker).get(f"{API}/?page_size=100").json()["items"]]
        assert "legacy-draft" in owner_titles and "legacy-draft" not in other_titles

    def test_platform_super_admin_sees_everything(self, world):
        draft = make_event(world.db, world.tenant_b, world.ent_b, status="pending_approval", title="queue-item")
        client = client_for(world.db, super_admin_user())
        assert client.get(f"{API}/{draft.id}").status_code == 200
        titles = [i["title"] for i in client.get(f"{API}/?status=pending_approval").json()["items"]]
        assert titles == ["queue-item"]

    def test_platform_admin_identified_only_remotely_still_sees_pending_events(self, world, monkeypatch):
        """A super-admin token without the claim is recognised through the identity service, but only
        when the request targets non-public content (so customer traffic never triggers that call)."""
        draft = make_event(world.db, world.tenant_b, world.ent_b, status="pending_approval", title="queue-item")
        calls = []

        def fake_resolve(user, access_token=None):
            calls.append(user["id"])
            return {**user, "role": "super_admin"}

        monkeypatch.setattr("app.services.super_admin_identity.resolve_platform_super_admin_user", fake_resolve)
        claimless = {"id": str(uuid4()), "role": None, "email": "platform@example.com"}
        client = client_for(world.db, claimless)
        assert client.get(f"{API}/?status=published").status_code == 200
        assert calls == []  # public browsing did not trigger the remote check
        assert [i["title"] for i in client.get(f"{API}/?status=pending_approval").json()["items"]] == ["queue-item"]
        assert client.get(f"{API}/{draft.id}").status_code == 200

    def test_internal_fields_are_hidden_from_the_public_and_shown_to_owners(self, world):
        public = client_for(world.db, None).get(f"{API}/{world.event.id}").json()
        assert public["last_admin_notes"] is None
        assert public["requires_reapproval"] is None
        assert public["organiser_contact"] is None
        assert public["organiser_name"] == "Org"  # ordinary public fields are untouched
        listed = client_for(world.db, None).get(f"{API}/").json()["items"][0]
        assert listed["last_admin_notes"] is None and listed["organiser_contact"] is None
        owned = client_for(world.db, world.owner).get(f"{API}/{world.event.id}").json()
        assert owned["last_admin_notes"] == "internal reviewer note"
        assert owned["organiser_contact"] == "organiser@example.com"
        # another tenant's staff: the internal review notes stay owner-only...
        cross = client_for(world.db, world.attacker).get(f"{API}/{world.event.id}").json()
        assert cross["last_admin_notes"] is None and cross["requires_reapproval"] is None
        # ...while authenticated staff keep organiser_contact (so an owner whose tenant could not be
        # resolved is never handed null and cannot wipe it by saving the edit form)
        assert cross["organiser_contact"] == "organiser@example.com"
        # customers are treated like the public
        customer = client_for(world.db, customer_user("c@example.com")).get(f"{API}/{world.event.id}").json()
        assert customer["organiser_contact"] is None and customer["last_admin_notes"] is None

    def test_owner_with_an_unresolvable_tenant_is_never_handed_null_organiser_contact(self, world):
        claimless_owner = {"id": str(uuid4()), "role": "admin", "email": "owner@example.com"}
        body = client_for(world.db, claimless_owner).get(f"{API}/{world.event.id}").json()
        assert body["organiser_contact"] == "organiser@example.com"
        assert body["last_admin_notes"] is None  # read-only internal field; no data-loss path

    def test_response_shape_is_unchanged(self, world):
        body = client_for(world.db, None).get(f"{API}/{world.event.id}").json()
        for key in ("id", "tenant_id", "title", "status", "sessions", "ticket_types", "available_seats",
                    "is_full", "registration_open", "meeting_link", "last_admin_notes", "organiser_contact"):
            assert key in body
        assert body["meeting_link"] == "protected"

    def test_ics_follows_the_same_visibility_rule(self, world):
        draft = make_event(world.db, world.tenant_b, world.ent_b, status="draft")
        assert client_for(world.db, None).get(f"{API}/{world.event.id}/calendar.ics").status_code == 200
        assert client_for(world.db, None).get(f"{API}/{draft.id}/calendar.ics").status_code == 404
        assert client_for(world.db, world.owner).get(f"{API}/{draft.id}/calendar.ics").status_code == 200
        assert client_for(world.db, None).get(f"{API}/{draft.id}/sessions/x/calendar.ics").status_code == 404


# ===========================================================================
# 4. Session meeting-link protection
# ===========================================================================


class TestSessionMeetingLinks:
    def sessions(self, world, user):
        resp = client_for(world.db, user).get(f"{API}/{world.event.id}/sessions")
        return resp

    def test_anonymous_cannot_call_the_endpoint(self, world):
        assert self.sessions(world, None).status_code == 401

    def test_authenticated_non_registered_user_gets_protected(self, world):
        resp = self.sessions(world, customer_user("stranger@example.com"))
        assert resp.status_code == 200
        body = resp.json()
        assert SESSION_LINK not in resp.text
        assert body[0]["meeting_link"] == "protected"
        assert body[0]["title"] == "Keynote" and body[0]["id"] == "sess-1"  # shape preserved

    def test_registered_participant_sees_protected_and_gets_the_real_link_only_via_the_protected_endpoint(self, world):
        me = customer_user("victim@example.com")
        listing = self.sessions(world, me)
        assert listing.status_code == 200 and SESSION_LINK not in listing.text
        assert listing.json()[0]["meeting_link"] == "protected"
        real = client_for(world.db, me).get(f"{API}/{world.event.id}/sessions/sess-1/meeting-link")
        assert real.status_code == 200
        assert real.json()["meeting_link"] == SESSION_LINK
        main = client_for(world.db, me).get(f"{API}/{world.event.id}/meeting-link")
        assert main.status_code == 200 and main.json()["meeting_link"] == "https://meet.example.com/main-room"

    def test_unregistered_user_cannot_use_the_protected_endpoints(self, world):
        stranger = customer_user("stranger@example.com")
        assert client_for(world.db, stranger).get(f"{API}/{world.event.id}/sessions/sess-1/meeting-link").status_code == 403
        assert client_for(world.db, stranger).get(f"{API}/{world.event.id}/meeting-link").status_code == 403

    def test_same_tenant_provider_gets_the_stored_sessions(self, world):
        provider = staff_user(world.tenant_b, "provider")
        resp = self.sessions(world, provider)
        assert resp.status_code == 200
        assert resp.json()[0]["meeting_link"] == SESSION_LINK  # organisers manage the real value
        assert client_for(world.db, provider).get(f"{API}/{world.event.id}/meeting-link").status_code == 200

    def test_other_tenant_provider_gets_protected_and_is_blocked_from_the_link_endpoints(self, world):
        provider = staff_user(world.tenant_a, "provider")
        resp = self.sessions(world, provider)
        assert resp.status_code == 200
        assert SESSION_LINK not in resp.text and resp.json()[0]["meeting_link"] == "protected"
        assert client_for(world.db, provider).get(f"{API}/{world.event.id}/meeting-link").status_code == 403
        assert client_for(world.db, provider).get(f"{API}/{world.event.id}/sessions/sess-1/meeting-link").status_code == 403

    def test_staff_who_also_registered_elsewhere_keep_participant_access(self, world):
        """A provider of tenant A that bought a ticket to tenant B's event is a participant there."""
        provider = staff_user(world.tenant_a, "provider", email="dual@example.com")
        make_registration(world.db, world.event, "dual@example.com")
        assert client_for(world.db, provider).get(f"{API}/{world.event.id}/meeting-link").status_code == 200

    def test_non_published_event_sessions_are_hidden_from_strangers(self, world):
        draft = make_event(world.db, world.tenant_b, world.ent_b, status="draft",
                           sessions=[{"id": "s", "title": "x", "meeting_link": SESSION_LINK}])
        assert client_for(world.db, customer_user("s@example.com")).get(f"{API}/{draft.id}/sessions").status_code == 404

    def test_get_sessions_does_not_write(self, world):
        """No write-on-read: legacy id-less sessions are not patched (or committed) by a GET."""
        legacy = make_event(world.db, world.tenant_b, world.ent_b, sessions=[{"title": "legacy", "meeting_link": SESSION_LINK}])
        commits = []
        original_commit = world.db.commit
        world.db.commit = lambda: (commits.append(1), original_commit())[1]
        resp = client_for(world.db, customer_user("s@example.com")).get(f"{API}/{legacy.id}/sessions")
        assert resp.status_code == 200 and resp.json()[0]["meeting_link"] == "protected"
        assert commits == []
        world.db.expire_all()
        assert "id" not in world.db.get(Event, legacy.id).sessions[0]

    def test_session_mutations_backfill_legacy_ids(self, world):
        legacy = make_event(world.db, world.tenant_b, world.ent_b, sessions=[{"title": "legacy", "session_date": world.session_date}])
        resp = client_for(world.db, world.owner).post(f"{API}/{legacy.id}/sessions", json={"session_date": world.session_date, "title": "new"})
        assert resp.status_code == 201
        world.db.expire_all()
        assert all(s.get("id") for s in world.db.get(Event, legacy.id).sessions)

    def test_session_create_update_delete_still_work_for_the_owner(self, world):
        client = client_for(world.db, world.owner)
        created = client.post(f"{API}/{world.event.id}/sessions", json={"session_date": world.session_date, "title": "Panel"})
        assert created.status_code == 201
        sid = created.json()["id"]
        assert client.put(f"{API}/{world.event.id}/sessions/{sid}", json={"title": "Panel 2"}).status_code == 200
        assert client.delete(f"{API}/{world.event.id}/sessions/{sid}").status_code == 200


# ===========================================================================
# 5. Customer-level IDORs: cancel / refund / QR
# ===========================================================================


class TestCustomerIdor:
    def test_customer_can_cancel_own_registration(self, world):
        resp = client_for(world.db, customer_user("victim@example.com")).delete(f"{API}/{world.event.id}/registrations/{world.reg.id}")
        assert resp.status_code == 200, resp.text
        world.db.expire_all()
        assert world.db.get(EventRegistration, world.reg.id).status == "cancelled"

    def test_mixed_case_email_matches(self, world):
        resp = client_for(world.db, customer_user("  VICTIM@Example.COM ")).delete(f"{API}/{world.event.id}/registrations/{world.reg.id}")
        assert resp.status_code == 200, resp.text

    def test_another_customer_cannot_cancel_it(self, world):
        before = snapshot(world.db)
        resp = client_for(world.db, customer_user("mallory@example.com")).delete(f"{API}/{world.event.id}/registrations/{world.reg.id}")
        assert resp.status_code == 403
        assert snapshot(world.db) == before

    def test_registration_of_another_event_is_not_found(self, world):
        other_event = make_event(world.db, world.tenant_b, world.ent_b, title="other")
        resp = client_for(world.db, customer_user("victim@example.com")).delete(f"{API}/{other_event.id}/registrations/{world.reg.id}")
        assert resp.status_code == 404

    def test_cross_tenant_staff_cannot_cancel(self, world):
        before = snapshot(world.db)
        resp = client_for(world.db, world.attacker).delete(f"{API}/{world.event.id}/registrations/{world.reg.id}")
        assert resp.status_code == 403
        assert snapshot(world.db) == before

    def test_same_tenant_staff_can_cancel(self, world):
        assert client_for(world.db, world.owner).delete(f"{API}/{world.event.id}/registrations/{world.reg.id}").status_code == 200

    def test_staff_can_still_cancel_their_own_registration_at_another_tenants_event(self, world):
        provider = staff_user(world.tenant_a, "provider", email="dual@example.com")
        mine = make_registration(world.db, world.event, "dual@example.com")
        assert client_for(world.db, provider).delete(f"{API}/{world.event.id}/registrations/{mine.id}").status_code == 200

    def test_attended_registration_still_cannot_be_cancelled(self, world):
        attended = make_registration(world.db, world.event, "here@example.com", status="attended")
        resp = client_for(world.db, customer_user("here@example.com")).delete(f"{API}/{world.event.id}/registrations/{attended.id}")
        assert resp.status_code == 400

    # -- refund
    def test_customer_can_request_refund_of_own_order(self, world):
        resp = client_for(world.db, customer_user("victim@example.com")).post(
            f"{API}/{world.event.id}/registrations/{world.order.id}/refund", json={"reason": "cannot attend"})
        assert resp.status_code == 200, resp.text
        world.db.expire_all()
        assert world.db.get(EventOrder, world.order.id).status == "refund_requested"

    def test_customer_cannot_refund_someone_elses_order_or_registration(self, world):
        before = snapshot(world.db)
        mallory = client_for(world.db, customer_user("mallory@example.com"))
        for target in (world.order.id, world.reg.id):
            resp = mallory.post(f"{API}/{world.event.id}/registrations/{target}/refund", json={})
            assert resp.status_code == 403, (target, resp.text)
        assert snapshot(world.db) == before

    def test_refund_with_id_from_another_event_is_not_found(self, world):
        other_event = make_event(world.db, world.tenant_b, world.ent_b)
        resp = client_for(world.db, customer_user("victim@example.com")).post(
            f"{API}/{other_event.id}/registrations/{world.order.id}/refund", json={})
        assert resp.status_code == 404

    def test_customer_cannot_use_the_staff_order_routes(self, world):
        client = client_for(world.db, customer_user("victim@example.com"))
        assert client.post(f"{API}/{world.event.id}/orders/{world.order.id}/refund", json={}).status_code == 403
        assert client.patch(f"{API}/{world.event.id}/orders/{world.order.id}/status", json={"status": "cancelled"}).status_code == 403

    # -- qr / waitlist
    def test_qr_image_only_for_the_owner_or_owning_staff(self, world):
        url = f"{API}/{world.event.id}/registrations/{world.reg.id}/qr"
        assert client_for(world.db, customer_user("mallory@example.com")).get(url).status_code == 403
        assert client_for(world.db, world.attacker).get(url).status_code == 403
        assert client_for(world.db, customer_user("VICTIM@example.com")).get(url).status_code in (200, 500)  # 500 only if qrcode lib is absent
        assert client_for(world.db, world.owner).get(url).status_code in (200, 500)

    def test_waitlist_leave_participant_owning_staff_and_others(self, world):
        url = f"{API}/{world.event.id}/waitlist/{world.wait.id}"
        assert client_for(world.db, customer_user("mallory@example.com")).delete(url).status_code == 404
        assert client_for(world.db, world.attacker).delete(url).status_code == 403
        assert client_for(world.db, super_admin_user()).delete(url).status_code == 200


# ===========================================================================
# 6. Templates
# ===========================================================================


class TestTemplateTenantIsolation:
    def _create(self, world, user, **extra):
        body = {"name": "T", "template_data": {"title": "Summit", "category": "Wellness"}, **extra}
        return client_for(world.db, user).post(f"{API}/templates", json=body)

    def test_template_is_created_under_the_callers_tenant(self, world):
        resp = self._create(world, world.owner)
        assert resp.status_code == 201, resp.text
        assert resp.json()["tenant_id"] == str(world.tenant_b)

    def test_supplied_foreign_tenant_id_is_rejected(self, world):
        assert self._create(world, world.attacker, tenant_id=str(world.tenant_b)).status_code == 403

    def test_supplied_foreign_enterprise_is_rejected(self, world):
        assert self._create(world, world.attacker, enterprise_id=str(world.ent_b.id)).status_code == 403

    def test_no_tenant_claim_cannot_create(self, world):
        assert self._create(world, {"id": str(uuid4()), "role": "provider", "email": "x@example.com"}, tenant_id=str(world.tenant_b)).status_code == 403

    def test_listing_only_returns_the_callers_templates(self, world):
        self._create(world, world.owner)
        self._create(world, world.attacker)
        mine_b = client_for(world.db, world.owner).get(f"{API}/templates").json()
        mine_a = client_for(world.db, world.attacker).get(f"{API}/templates").json()
        assert {t["tenant_id"] for t in mine_b} == {str(world.tenant_b)}
        assert {t["tenant_id"] for t in mine_a} == {str(world.tenant_a)}

    def test_user_without_tenant_claim_does_not_get_all_templates(self, world):
        """The fail-open: no claim used to mean 'no filter' = every tenant's templates."""
        self._create(world, world.owner)
        self._create(world, world.attacker)
        no_claim = {"id": str(uuid4()), "role": "provider", "email": "x@example.com"}
        assert client_for(world.db, no_claim).get(f"{API}/templates").status_code == 403

    def test_customer_cannot_list_templates(self, world):
        self._create(world, world.owner)
        assert client_for(world.db, customer_user("c@example.com")).get(f"{API}/templates").status_code == 403

    def test_cross_tenant_get_update_delete_apply_are_blocked(self, world):
        tmpl_id = self._create(world, world.owner).json()["id"]
        attacker = client_for(world.db, world.attacker)
        assert attacker.get(f"{API}/templates/{tmpl_id}").status_code == 403
        assert attacker.put(f"{API}/templates/{tmpl_id}", json={"name": "hijacked"}).status_code == 403
        assert attacker.post(f"{API}/templates/{tmpl_id}/apply", json={}).status_code == 403
        assert attacker.delete(f"{API}/templates/{tmpl_id}").status_code == 403
        world.db.expire_all()
        assert world.db.query(EventTemplate).count() == 1
        assert world.db.query(EventTemplate).first().name == "T"

    def test_no_claim_user_cannot_touch_existing_templates(self, world):
        tmpl_id = self._create(world, world.owner).json()["id"]
        no_claim = client_for(world.db, {"id": str(uuid4()), "role": "provider", "email": "x@example.com"})
        assert no_claim.get(f"{API}/templates/{tmpl_id}").status_code == 403
        assert no_claim.put(f"{API}/templates/{tmpl_id}", json={"name": "x"}).status_code == 403
        assert no_claim.delete(f"{API}/templates/{tmpl_id}").status_code == 403

    def test_owner_can_get_update_delete(self, world):
        tmpl_id = self._create(world, world.owner).json()["id"]
        owner = client_for(world.db, world.owner)
        assert owner.get(f"{API}/templates/{tmpl_id}").status_code == 200
        assert owner.put(f"{API}/templates/{tmpl_id}", json={"name": "renamed"}).json()["name"] == "renamed"
        assert owner.delete(f"{API}/templates/{tmpl_id}").status_code == 200

    def test_platform_super_admin_listing_is_explicit(self, world):
        self._create(world, world.owner)
        self._create(world, world.attacker)
        assert len(client_for(world.db, super_admin_user()).get(f"{API}/templates").json()) == 2

    def test_template_with_no_resolvable_owner_is_platform_only(self, world):
        orphan = EventTemplate(name="orphan", template_data={"title": "x"}, tenant_id=None, enterprise_id=None)
        world.db.add(orphan)
        world.db.commit()
        assert client_for(world.db, world.owner).get(f"{API}/templates/{orphan.id}").status_code == 403
        assert client_for(world.db, world.owner).get(f"{API}/templates").json() == []


# ===========================================================================
# 7. reports/summary + auto-complete scope
# ===========================================================================


class TestSummaryAndAutoCompleteScope:
    def test_summary_is_scoped_to_the_callers_tenant(self, world):
        make_event(world.db, world.tenant_a, world.ent_a, title="A1")
        make_event(world.db, world.tenant_a, world.ent_a, title="A2", status="draft")
        b_body = client_for(world.db, world.owner).get(f"{API}/reports/summary").json()
        a_body = client_for(world.db, world.attacker).get(f"{API}/reports/summary").json()
        assert b_body["total_events"] == 1  # only tenant B's single event
        assert a_body["total_events"] == 2  # and only tenant A's two
        assert b_body["total_registrations"] == 1 and a_body["total_registrations"] == 0

    def test_enterprise_id_query_cannot_widen_the_scope(self, world):
        resp = client_for(world.db, world.attacker).get(f"{API}/reports/summary", params={"enterprise_id": str(world.ent_b.id)})
        assert resp.status_code == 200
        assert resp.json()["total_events"] == 0
        assert resp.json()["total_registrations"] == 0

    def test_summary_without_a_resolvable_tenant_is_refused_not_global(self, world):
        no_claim = {"id": str(uuid4()), "role": "provider", "email": "x@example.com"}
        assert client_for(world.db, no_claim).get(f"{API}/reports/summary").status_code == 403

    def test_service_never_defaults_to_platform_wide(self, world):
        from fastapi import HTTPException
        from app.services.event_service import get_event_summary_service

        with pytest.raises(HTTPException) as exc:
            get_event_summary_service(world.db)
        assert exc.value.status_code == 403
        assert get_event_summary_service(world.db, platform_wide=True)["total_events"] == 1

    def _past(self, world, tenant, ent, title):
        return make_event(world.db, tenant, ent, title=title,
                          start_date=datetime.utcnow() - timedelta(days=3), end_date=datetime.utcnow() - timedelta(days=2))

    def test_auto_complete_only_touches_the_callers_tenant(self, world):
        mine = self._past(world, world.tenant_a, world.ent_a, "mine")
        theirs = self._past(world, world.tenant_b, world.ent_b, "theirs")
        resp = client_for(world.db, world.attacker).post(f"{API}/auto-complete")
        assert resp.status_code == 200 and resp.json() == {"auto_completed": 1}
        world.db.expire_all()
        assert world.db.get(Event, mine.id).status == "completed"
        assert world.db.get(Event, theirs.id).status == "published"
        actor = world.db.query(EventAudit).filter(EventAudit.action == "auto_complete").one().changed_by
        assert actor == world.attacker["id"]

    def test_auto_complete_enterprise_param_cannot_reach_other_tenants(self, world):
        theirs = self._past(world, world.tenant_b, world.ent_b, "theirs")
        resp = client_for(world.db, world.attacker).post(f"{API}/auto-complete", params={"enterprise_id": str(world.ent_b.id)})
        assert resp.json() == {"auto_completed": 0}
        world.db.expire_all()
        assert world.db.get(Event, theirs.id).status == "published"

    def test_auto_complete_without_tenant_is_refused(self, world):
        no_claim = {"id": str(uuid4()), "role": "admin", "email": "x@example.com"}
        assert client_for(world.db, no_claim).post(f"{API}/auto-complete").status_code == 403

    def test_platform_super_admin_auto_complete_is_platform_wide(self, world):
        self._past(world, world.tenant_a, world.ent_a, "a")
        self._past(world, world.tenant_b, world.ent_b, "b")
        assert client_for(world.db, super_admin_user()).post(f"{API}/auto-complete").json() == {"auto_completed": 2}


# ===========================================================================
# 8. Event admin routes (Enterprise Admin fallback is tenant-scoped)
# ===========================================================================


class TestAdminEventRoutes:
    """get_current_super_admin's Enterprise-Admin fallback is left as is; Event routes scope it."""

    @pytest.fixture
    def admin_client(self, world):
        from app.core.dependencies import get_current_super_admin
        from app.main import app

        def as_(user):
            client = client_for(world.db, user)
            app.dependency_overrides[get_current_super_admin] = lambda: user
            return client

        return as_

    @pytest.fixture
    def pending(self, world):
        return make_event(world.db, world.tenant_b, world.ent_b, status="pending_approval", title="pending-b")

    @pytest.mark.parametrize("action,body", [("approve", None), ("reject", {"reason": "no"}), ("request-changes", {"reason": "fix"}), ("publish", None)])
    def test_enterprise_admin_fallback_cannot_act_on_another_tenants_event(self, world, admin_client, pending, action, body):
        before = snapshot(world.db)
        resp = admin_client(world.attacker).post(f"/api/v1/admin/events/{pending.id}/{action}", json=body)
        assert resp.status_code == 403
        assert snapshot(world.db) == before

    def test_enterprise_admin_fallback_can_still_act_on_its_own_event(self, world, admin_client, pending):
        resp = admin_client(world.owner).post(f"/api/v1/admin/events/{pending.id}/approve")
        assert resp.status_code == 200, resp.text
        world.db.expire_all()
        assert world.db.get(Event, pending.id).status == "approved"

    def test_platform_super_admin_acts_across_tenants(self, world, admin_client, pending):
        resp = admin_client(super_admin_user()).post(f"/api/v1/admin/events/{pending.id}/approve")
        assert resp.status_code == 200, resp.text

    def test_pending_queue_is_tenant_scoped_for_the_fallback_and_global_for_the_platform(self, world, admin_client, pending):
        make_event(world.db, world.tenant_a, world.ent_a, status="pending_approval", title="pending-a")
        titles = lambda user: sorted(i["title"] for i in admin_client(user).get("/api/v1/admin/events/pending").json()["items"])
        assert titles(world.attacker) == ["pending-a"]
        assert titles(world.owner) == ["pending-b"]
        assert titles(super_admin_user()) == ["pending-a", "pending-b"]

    def test_event_audit_history_is_tenant_scoped_for_the_fallback(self, world, admin_client):
        assert admin_client(world.attacker).get(f"/api/v1/admin/event-audits/{world.event.id}").status_code == 403
        assert admin_client(world.owner).get(f"/api/v1/admin/event-audits/{world.event.id}").status_code == 200
        assert admin_client(super_admin_user()).get(f"/api/v1/admin/event-audits/{world.event.id}").status_code == 200


# ===========================================================================
# 9. Review moderation is event-scoped
# ===========================================================================


class TestReviewModeration:
    def test_review_from_another_event_cannot_be_moderated_through_your_event(self, world):
        mine = make_event(world.db, world.tenant_a, world.ent_a)
        resp = client_for(world.db, world.attacker).patch(f"{API}/{mine.id}/reviews/{world.review.id}/moderate", json={"action": "rejected"})
        assert resp.status_code == 404
        world.db.expire_all()
        assert world.db.get(EventFeedback, world.review.id).moderation_status == "pending"

    def test_owner_admin_can_moderate_their_own_events_review(self, world):
        resp = client_for(world.db, world.owner).patch(f"{API}/{world.event.id}/reviews/{world.review.id}/moderate", json={"action": "approved"})
        assert resp.status_code == 200
        world.db.expire_all()
        assert world.db.get(EventFeedback, world.review.id).moderation_status == "approved"

    def test_provider_role_still_cannot_moderate(self, world):
        provider = staff_user(world.tenant_b, "provider")
        assert client_for(world.db, provider).patch(f"{API}/{world.event.id}/reviews/{world.review.id}/moderate", json={"action": "approved"}).status_code == 403
