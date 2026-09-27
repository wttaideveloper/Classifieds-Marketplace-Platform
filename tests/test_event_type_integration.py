"""
Event Management Phase 2.2 (evolved) — dynamic Event Types integration: a Super-Admin-created,
non-legacy Event Type drives real event creation/update end to end, and every later Phase (2.3-2.7)
keeps working for an event created under one.

Complements tests/test_event_modules_config.py (pure) and tests/test_event_phase_2_2_configuration.py
(the 7 seeded/legacy types) — this file is specifically about a *custom* Event Type created through the
new CRUD, proving the backend never hardcodes the vocabulary, and about the two behaviors approved
alongside the plan:

- a paid event whose Event Type does not allow tickets is a clear 422, never a silent override;
- changing an event's type is rejected (422) when its persisted modules would become incompatible with
  the new type, and is a no-op (no modules rewritten) when they remain compatible.

Run:
    pytest tests/test_event_type_integration.py -v
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from event_sql_support import (
    API,
    client_for,
    customer_user,
    make_enterprise,
    make_event,
    make_session,
    paid_ticket,
    reset_overrides,
    silence_side_effects,
    staff_user,
    super_admin_user,
)

TYPES_API = "/api/v1/event-types"
SPEC_KEYS = ["registration", "tickets", "sessions", "check_in", "online_meeting", "custom_questions", "meals", "accommodation"]


def flags(*enabled):
    return {key: key in enabled for key in SPEC_KEYS}


ALL_ALLOWED = flags(*SPEC_KEYS)
NONE_REQUIRED = flags()


@pytest.fixture
def env(monkeypatch):
    apply_async = silence_side_effects(monkeypatch)
    db = make_session()
    tenant = uuid4()
    ent = make_enterprise(db, tenant)
    monkeypatch.setattr(
        "app.services.event_form_config_service.apply_form_configuration_to_event_data",
        lambda _db, data, user: {"tenant_id": tenant, "enterprise_id": ent.id, "form_configuration_id": None,
                                 "form_configuration_version_id": None, "custom_values": []},
    )
    owner = staff_user(tenant, "admin")
    yield SimpleNamespace(db=db, tenant=tenant, ent=ent, owner=owner, apply_async=apply_async)
    reset_overrides()
    db.close()


def staff_client(env):
    """A fresh client authenticated as the tenant owner, built at the point of use — never cached.

    fastapi_app.dependency_overrides is one global dict shared by every client_for(...) call, so a client
    object built once and reused AFTER a different identity's client_for(...) call (e.g. the Super Admin
    call inside create_type) would silently pick up that LATER identity instead of its own."""
    return client_for(env.db, env.owner)


def create_type(env, key, *, default_modules=None, allowed_modules=None, required_modules=None, active=True):
    payload = {"key": key, "name": key.replace("_", " ").title(), "active": active,
              "default_modules": default_modules if default_modules is not None else flags("registration", "check_in")}
    if allowed_modules is not None:
        payload["allowed_modules"] = allowed_modules
    if required_modules is not None:
        payload["required_modules"] = required_modules
    resp = client_for(env.db, super_admin_user()).post(f"{TYPES_API}/", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def body(**extra):
    start = datetime.utcnow() + timedelta(days=5)
    return {"title": "Summit", "category": "Wellness", "start_date": start.isoformat(),
            "end_date": (start + timedelta(hours=4)).isoformat(), "status": "draft", **extra}


def create_event(env, **extra):
    return staff_client(env).post(f"{API}/", json=body(**extra))


# ===========================================================================
# a custom, non-legacy Event Type drives real event creation
# ===========================================================================


class TestCustomEventTypeCreatesRealEvents:
    def test_creating_an_event_with_a_brand_new_event_type_applies_its_defaults(self, env):
        create_type(env, "medical_camp", default_modules=flags("registration", "check_in", "meals"))
        resp = create_event(env, event_type="medical_camp")
        assert resp.status_code == 201, resp.text
        assert resp.json()["event_type"] == "medical_camp"
        assert resp.json()["modules"] == flags("registration", "check_in", "meals")

    def test_the_backend_never_hardcodes_the_vocabulary(self, env):
        """A key that is neither one of the 7 seeded types nor a Python constant anywhere works exactly
        like any of them, purely because a database row for it exists."""
        create_type(env, "corporate_town_hall")
        assert create_event(env, event_type="corporate_town_hall").status_code == 201

    def test_an_override_the_new_type_does_not_allow_is_rejected(self, env):
        create_type(env, "medical_camp", allowed_modules=flags("registration", "check_in"))
        resp = create_event(env, event_type="medical_camp", modules={"meals": True})
        assert resp.status_code == 422 and "does not allow" in resp.json()["detail"] and "meals" in resp.json()["detail"]

    def test_disabling_a_required_module_is_rejected(self, env):
        create_type(env, "medical_camp", required_modules=flags("registration"))
        resp = create_event(env, event_type="medical_camp", modules={"registration": False})
        assert resp.status_code == 422 and "requires" in resp.json()["detail"] and "registration" in resp.json()["detail"]

    def test_deactivating_a_type_stops_new_events_but_not_reads_of_existing_ones(self, env):
        created = create_type(env, "medical_camp")
        existing = create_event(env, event_type="medical_camp").json()["id"]
        client_for(env.db, super_admin_user()).patch(f"{TYPES_API}/{created['id']}", json={"active": False})
        assert create_event(env, event_type="medical_camp").status_code == 422
        assert staff_client(env).get(f"{API}/{existing}").json()["event_type"] == "medical_camp"  # unaffected


# ===========================================================================
# decision 3: paid + Event Type disallows tickets
# ===========================================================================


class TestPaidEventVsAllowedTickets:
    def test_a_paid_event_under_a_ticketless_and_ticket_disallowing_type_is_a_clear_422(self, env):
        create_type(env, "prayer_meeting", default_modules=flags("registration", "check_in"),
                    allowed_modules=flags("registration", "check_in"))  # tickets not allowed at all
        resp = create_event(env, event_type="prayer_meeting", pricing_type="paid", price="10")
        assert resp.status_code == 422
        assert "does not allow tickets" in resp.json()["detail"] and "paid" in resp.json()["detail"]

    def test_the_same_type_is_fine_for_a_free_event(self, env):
        create_type(env, "prayer_meeting", default_modules=flags("registration", "check_in"),
                    allowed_modules=flags("registration", "check_in"))
        assert create_event(env, event_type="prayer_meeting").status_code == 201

    def test_a_paid_event_under_a_type_that_allows_tickets_still_gets_the_existing_safeguard(self, env):
        create_type(env, "medical_camp")  # allowed_modules omitted -> everything allowed (seed-equivalent)
        resp = create_event(env, event_type="medical_camp", pricing_type="paid", price="10")
        assert resp.status_code == 201, resp.text
        assert resp.json()["modules"]["tickets"] is True

    def test_on_update_too(self, env):
        create_type(env, "prayer_meeting", default_modules=flags("registration", "check_in"),
                    allowed_modules=flags("registration", "check_in"))
        event_id = create_event(env).json()["id"]  # legacy, free
        resp = staff_client(env).put(f"{API}/{event_id}", json={"pricing_type": "paid", "price": "10", "event_type": "prayer_meeting"})
        assert resp.status_code == 422 and "does not allow tickets" in resp.json()["detail"]


# ===========================================================================
# decision 4: changing type must not create (or keep) an invalid configuration
# ===========================================================================


class TestChangingEventTypeRevalidatesTheConfiguration:
    def test_changing_to_an_incompatible_type_is_rejected_and_nothing_changes(self, env):
        create_type(env, "conference_like", default_modules=flags("registration", "tickets", "check_in"))
        create_type(env, "no_tickets_allowed", allowed_modules=flags("registration", "check_in"))
        event_id = create_event(env, event_type="conference_like", pricing_type="paid", price="10").json()["id"]
        before = staff_client(env).get(f"{API}/{event_id}").json()["modules"]
        resp = staff_client(env).put(f"{API}/{event_id}", json={"event_type": "no_tickets_allowed"})
        assert resp.status_code == 422
        assert "does not allow" in resp.json()["detail"] and "tickets" in resp.json()["detail"]
        after = staff_client(env).get(f"{API}/{event_id}").json()
        assert after["modules"] == before and after["event_type"] == "conference_like"  # nothing was applied

    def test_changing_to_a_compatible_type_succeeds_without_rewriting_modules(self, env):
        create_type(env, "type_a", default_modules=flags("registration", "check_in", "meals"))
        create_type(env, "type_b")  # fully permissive: type_a's persisted modules are compatible
        event_id = create_event(env, event_type="type_a").json()["id"]
        before = staff_client(env).get(f"{API}/{event_id}").json()["modules"]
        resp = staff_client(env).put(f"{API}/{event_id}", json={"event_type": "type_b"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["event_type"] == "type_b" and resp.json()["modules"] == before

    def test_overrides_sent_with_an_incompatible_type_change_are_also_rejected(self, env):
        """The override alone would be fine; a DIFFERENT untouched persisted key is what conflicts."""
        create_type(env, "type_a", default_modules=flags("registration", "sessions", "check_in"))
        create_type(env, "no_sessions", allowed_modules=flags("registration", "check_in", "meals"))
        event_id = create_event(env, event_type="type_a").json()["id"]
        resp = staff_client(env).put(f"{API}/{event_id}", json={"event_type": "no_sessions", "modules": {"meals": True}})
        assert resp.status_code == 422 and "sessions" in resp.json()["detail"]

    def test_a_required_module_missing_from_the_persisted_configuration_blocks_the_change(self, env):
        create_type(env, "type_a", default_modules=flags("check_in"))  # registration off by default here
        create_type(env, "needs_registration", default_modules=flags("registration"), required_modules=flags("registration"))
        event_id = create_event(env, event_type="type_a").json()["id"]  # persisted: registration=False
        resp = staff_client(env).put(f"{API}/{event_id}", json={"event_type": "needs_registration"})
        assert resp.status_code == 422 and "requires" in resp.json()["detail"] and "registration" in resp.json()["detail"]


# ===========================================================================
# regression: a custom Event Type across every later phase (2.1, 2.3-2.7)
# ===========================================================================


class TestRegressionAcrossLaterPhases:
    @pytest.fixture
    def custom_type_event(self, env):
        create_type(env, "custom_gathering", default_modules=flags("registration", "check_in", "meals", "accommodation"))
        event = make_event(
            env.db, env.tenant, env.ent, status="published", event_type="custom_gathering",
            modules=flags("registration", "check_in", "meals", "accommodation"),
            meals={"options": [{"id": "lunch", "name": "Lunch", "active": True}]},
            accommodation={"options": [{"id": "shared-room", "name": "Shared Room", "active": True}]},
            capacity="20",
        )
        return event

    def test_free_registration_with_meals_and_accommodation_still_works(self, env, custom_type_event):
        resp = client_for(env.db, customer_user("a@example.com")).post(
            f"{API}/{custom_type_event.id}/registrations",
            json={"participant_name": "A", "participant_email": "a@example.com",
                  "meal_selections": ["lunch"], "accommodation_selections": ["shared-room"]})
        assert resp.status_code == 201, resp.text
        assert resp.json()["meal_selections"] == ["lunch"] and resp.json()["accommodation_selections"] == ["shared-room"]

    def test_walk_in_still_works(self, env, custom_type_event):
        resp = staff_client(env).post(f"{API}/{custom_type_event.id}/walk-in", json={
            "participant_name": "W", "participant_email": "w@example.com", "meal_selections": ["lunch"]})
        assert resp.status_code == 201, resp.text

    def test_paid_checkout_still_works(self, env):
        create_type(env, "custom_paid_type")
        event = make_event(
            env.db, env.tenant, env.ent, status="published", event_type="custom_paid_type",
            modules=flags("registration", "tickets", "check_in"), pricing_type="paid", price="500",
            ticket_types=[paid_ticket("500", ticket_id="general")], capacity="10",
        )
        resp = client_for(env.db, customer_user("buyer@example.com")).post(f"{API}/{event.id}/checkout", json={
            "participant_name": "Buyer", "participant_email": "buyer@example.com", "ticket_type_id": "general", "quantity": 1})
        assert resp.status_code == 201, resp.text

    def test_check_in_still_works(self, env, custom_type_event):
        reg = client_for(env.db, customer_user("a@example.com")).post(
            f"{API}/{custom_type_event.id}/registrations", json={"participant_name": "A", "participant_email": "a@example.com"})
        resp = staff_client(env).post(f"{API}/{custom_type_event.id}/check-in", json={"qr_code": reg.json()["qr_code"]})
        assert resp.status_code == 200, resp.text

    def test_session_check_in_still_works(self, env, custom_type_event):
        session_date = (datetime.utcnow() + timedelta(days=10)).date().isoformat()
        created = staff_client(env).post(f"{API}/{custom_type_event.id}/sessions", json={"session_date": session_date, "title": "S"})
        assert created.status_code == 201, created.text
        session_id = created.json()["id"] if "id" in created.json() else staff_client(env).get(f"{API}/{custom_type_event.id}").json()["sessions"][0]["id"]
        reg = client_for(env.db, customer_user("a@example.com")).post(
            f"{API}/{custom_type_event.id}/registrations", json={"participant_name": "A", "participant_email": "a@example.com"})
        resp = staff_client(env).post(f"{API}/{custom_type_event.id}/sessions/{session_id}/check-in", json={"registration_id": reg.json()["id"]})
        assert resp.status_code == 200, resp.text

    def test_attendee_dashboard_and_export_still_work(self, env, custom_type_event):
        client_for(env.db, customer_user("a@example.com")).post(
            f"{API}/{custom_type_event.id}/registrations",
            json={"participant_name": "A", "participant_email": "a@example.com", "meal_selections": ["lunch"]})
        attendees = staff_client(env).get(f"{API}/{custom_type_event.id}/attendees")
        assert attendees.status_code == 200 and attendees.json()["items"][0]["meal_selections"]
        dashboard = staff_client(env).get(f"{API}/{custom_type_event.id}/dashboard")
        assert dashboard.status_code == 200 and dashboard.json()["event"]["event_type"] == "custom_gathering"
        export = staff_client(env).get(f"{API}/{custom_type_event.id}/registrations/export")
        assert export.status_code == 200 and "meals" in export.text
