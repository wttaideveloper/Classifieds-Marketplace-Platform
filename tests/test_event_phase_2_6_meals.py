"""
Event Management Phase 2.6 — meals: configuration on the Event, selections on the registration, the update endpoint,
attendee management, the dashboard counts and the export.

Real (in-memory SQLite) database via tests/event_sql_support.py, so the persisted JSON, the JSON expansion behind the
counts, the transactional audit rows and the ownership checks run as SQL against the actual endpoints.

Covers: configuration (create/update, stable ids, retiring, the modules.meals switch, validation, echo-safe edits),
legacy events (no meals, no writes on read), online registration and walk-in selections through ONE validator, the
participant/organizer update endpoint, attendee list + CSV, dashboard counts, security + mass assignment, audit +
atomicity, and OpenAPI.

Run:
    pytest tests/test_event_phase_2_6_meals.py -v
"""
import csv
import io
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event as sa_event

from event_sql_support import (
    API,
    Event,
    EventAudit,
    EventOrder,
    EventRegistration,
    EventWaitlist,
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
import app.services.event_meal_service as meal_service
import app.services.event_walk_in_service as walk_in_service

LONG_AGO = datetime(2020, 1, 1)
ALL_OFF = {"registration": True, "tickets": False, "sessions": False, "check_in": True, "online_meeting": False,
           "custom_questions": False, "meals": False, "accommodation": False}
MEALS_ON = {**ALL_OFF, "meals": True}


def option(option_id, name, active=True, date=None, description=None):
    return {"id": option_id, "name": name, "description": description, "date": date, "active": active}


THREE = [option("breakfast", "Breakfast"), option("lunch", "Lunch"), option("dinner", "Dinner")]


@pytest.fixture
def env(monkeypatch):
    apply_async = silence_side_effects(monkeypatch)
    db = make_session()
    tenant = uuid4()
    ent = make_enterprise(db, tenant)
    monkeypatch.setattr(  # the Event Form machinery needs tables outside this harness (as in the Phase 2.2 tests)
        "app.services.event_form_config_service.apply_form_configuration_to_event_data",
        lambda _db, data, user: {"tenant_id": tenant, "enterprise_id": ent.id, "form_configuration_id": None,
                                 "form_configuration_version_id": None, "custom_values": []},
    )
    yield SimpleNamespace(db=db, tenant=tenant, ent=ent, owner=staff_user(tenant, "admin"), apply_async=apply_async)
    reset_overrides()
    db.close()


# ---------------------------------------------------------------------------- helpers


def owner_client(env):
    return client_for(env.db, env.owner)


def body(**extra):
    start = datetime.utcnow() + timedelta(days=5)
    return {"title": "Summit", "category": "Wellness", "start_date": start.isoformat(),
            "end_date": (start + timedelta(hours=4)).isoformat(), "status": "draft", **extra}


def create(env, **extra):
    return owner_client(env).post(f"{API}/", json=body(**extra))


def update(env, event_id, **fields):
    return owner_client(env).put(f"{API}/{event_id}", json=fields)


def row(env, event_id):
    env.db.expire_all()
    return env.db.get(Event, UUID(str(event_id)))


def ids(payload_or_options):
    options = payload_or_options["meals"]["options"] if "meals" in payload_or_options else payload_or_options
    return [o["id"] for o in options]


def meal_event(env, options=None, modules=MEALS_ON, meals="default", **overrides):
    stored = {"options": [dict(o) for o in (THREE if options is None else options)]} if meals == "default" else meals
    return make_event(env.db, env.tenant, env.ent, status="published", modules=modules, meals=stored, capacity="100", **overrides)


def register(env, event, email, selections=None, **extra):
    payload = {"participant_name": email.split("@")[0], "participant_email": email, **extra}
    if selections is not None:
        payload["meal_selections"] = selections
    return client_for(env.db, customer_user(email)).post(f"{API}/{event.id}/registrations", json=payload)


def walk_in(env, event, email, selections=None, **extra):
    payload = {"participant_name": "Walker", "participant_email": email, "check_in": False, **extra}
    if selections is not None:
        payload["meal_selections"] = selections
    return owner_client(env).post(f"{API}/{event.id}/walk-in", json=payload)


def patch_meals(env, event, registration_id, selections, client=None):
    return (client or owner_client(env)).patch(f"{API}/{event.id}/registrations/{registration_id}/meals", json={"meal_selections": selections})


def stored_selections(env, registration_id):
    env.db.expire_all()
    return env.db.get(EventRegistration, UUID(str(registration_id))).meal_selections


def audits(env, action=None):
    env.db.expire_all()
    query = env.db.query(EventAudit)
    if action:
        query = query.filter(EventAudit.action == action)
    return query.order_by(EventAudit.created_at).all()


def dashboard(env, event):
    resp = owner_client(env).get(f"{API}/{event.id}/dashboard")
    assert resp.status_code == 200, resp.text
    return resp.json()


def meal_counts(env, event):
    return {m["meal_id"]: m["selected_count"] for m in dashboard(env, event)["meals"]}


class Statements:
    """Counts SELECTs and records anything that writes."""

    def __init__(self, db):
        self.engine = db.get_bind()
        self.selects = 0
        self.writes = []

    def __enter__(self):
        sa_event.listen(self.engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc):
        sa_event.remove(self.engine, "before_cursor_execute", self._record)

    def _record(self, conn, cursor, statement, *args):
        keyword = statement.lstrip().split(None, 1)[0].upper()
        if keyword == "SELECT":
            self.selects += 1
        elif keyword in ("INSERT", "UPDATE", "DELETE"):
            self.writes.append(statement.split("\n")[0][:80])


def snapshot(env):
    """Everything a rejected meal request must leave alone."""
    env.db.expire_all()
    return (
        sorted((str(r.id), str(r.meal_selections), r.status) for r in env.db.query(EventRegistration)),
        sorted((str(e.id), str(e.meals), str(e.modules), e.title) for e in env.db.query(Event)),
        env.db.query(EventAudit).count(), env.db.query(EventOrder).count(), env.db.query(EventWaitlist).count(),
    )


# ===========================================================================
# 1-10. Configuration
# ===========================================================================


class TestConfiguration:
    def test_meals_can_be_enabled_through_the_event_type_default(self, env):
        resp = create(env, event_type="conference", meals={"options": [{"name": "Breakfast"}, {"name": "Lunch"}, {"name": "Dinner"}]})
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["modules"]["meals"] is True and data["meals"]["enabled"] is True
        assert [o["name"] for o in data["meals"]["options"]] == ["Breakfast", "Lunch", "Dinner"]
        assert all(o["active"] is True for o in data["meals"]["options"]) and len(set(ids(data))) == 3

    def test_meals_can_be_enabled_with_an_explicit_module_override(self, env):
        resp = create(env, event_type="other", modules={"meals": True}, meals={"options": [{"name": "Vegetarian"}, {"name": "Non-Vegetarian"}]})
        assert resp.status_code == 201, resp.text
        assert resp.json()["meals"]["enabled"] is True and len(resp.json()["meals"]["options"]) == 2

    def test_enabled_with_no_options_stores_nothing(self, env):
        data = create(env, event_type="camp").json()
        assert data["meals"] == {"enabled": True, "options": []}  # the type default turned meals on; nothing to choose yet
        assert row(env, data["id"]).meals is None

    def test_meals_disabled(self, env):
        data = create(env, event_type="conference", modules={"meals": False}).json()
        assert data["modules"]["meals"] is False and data["meals"] == {"enabled": False, "options": []}

    def test_an_event_that_says_nothing_about_meals_is_unchanged(self, env):
        data = create(env).json()
        assert data["meals"] == {"enabled": False, "options": []}
        stored = row(env, data["id"])
        assert stored.meals is None and stored.modules is None  # legacy: nothing was stored

    @pytest.mark.parametrize("extra", [
        {"event_type": "workshop"},  # the type default has meals off
        {"event_type": "conference", "modules": {"meals": False}},
        {},  # no type, no modules: a legacy-style event
        {"modules": {"registration": True}},
    ])
    def test_options_are_rejected_when_meals_are_off_rather_than_silently_enabling_them(self, env, extra):
        resp = create(env, meals={"options": [{"name": "Lunch"}]}, **extra)
        assert resp.status_code == 422 and "Meals are disabled" in resp.json()["detail"] and "modules.meals" in resp.json()["detail"]
        assert env.db.query(Event).count() == 0 and audits(env) == []

    def test_empty_options_are_accepted_while_meals_are_off(self, env):
        assert create(env, event_type="workshop", meals={"options": []}).status_code == 201

    @pytest.mark.parametrize("enabled, modules_meals, ok", [(True, True, True), (False, False, True), (True, False, False), (False, True, False)])
    def test_the_enabled_flag_is_only_a_mirror_of_modules_meals(self, env, enabled, modules_meals, ok):
        resp = create(env, event_type="other", modules={"meals": modules_meals}, meals={"enabled": enabled})
        assert (resp.status_code == 201) == ok, resp.text
        if not ok:
            assert "conflicts with modules.meals" in resp.json()["detail"] and env.db.query(Event).count() == 0

    def test_a_valid_configuration_round_trips_and_is_stored_compactly(self, env):
        data = create(env, event_type="camp", meals={"options": [
            {"id": "breakfast-day-1", "name": "Breakfast", "date": "2027-01-10", "description": "Served 7-9am"},
            {"name": "Snacks", "active": True}]}).json()
        first = data["meals"]["options"][0]
        assert first == {"id": "breakfast-day-1", "name": "Breakfast", "description": "Served 7-9am", "date": "2027-01-10", "active": True}
        stored = row(env, data["id"]).meals
        assert list(stored) == ["options"] and stored["options"][0] == first  # {"options": [...]}: no second "enabled" truth in storage
        assert "enabled" not in stored

    @pytest.mark.parametrize("meals", [
        {"options": "Lunch"}, {"options": ["Lunch"]}, {"options": [None]}, {"options": [{"name": "A"}, None]}, {"options": {"name": "A"}},
        {"options": [{"name": "A", "price": 5}]}, {"options": [{"name": "A", "capacity": 10}]}, {"vendor": "Acme"},
        {"options": [{"name": "A", "date": "tomorrow"}]}, {"options": [{"name": "A", "active": "yes"}]},
        {"options": [{"name": "x" * 101}]}, {"options": [{"name": "A", "description": "x" * 301}]},
        {"options": [{"name": f"m{i}"} for i in range(51)]}, {"enabled": "yes"}, "Lunch", 5, [],
    ])
    def test_malformed_configuration_is_rejected_and_nothing_is_created(self, env, meals):
        resp = create(env, event_type="camp", meals=meals)
        assert resp.status_code == 422, resp.text
        assert env.db.query(Event).count() == 0 and audits(env) == []

    def test_duplicate_meal_ids_are_rejected(self, env):
        resp = create(env, event_type="camp", meals={"options": [{"id": "lunch", "name": "Lunch"}, {"id": "LUNCH", "name": "Late lunch"}]})
        assert resp.status_code == 422 and "Duplicate meal option id" in resp.json()["detail"]
        assert env.db.query(Event).count() == 0

    def test_duplicate_options_are_rejected(self, env):
        resp = create(env, event_type="camp", meals={"options": [{"name": "Lunch"}, {"name": "lunch"}]})
        assert resp.status_code == 422 and "Duplicate meal option" in resp.json()["detail"]

    @pytest.mark.parametrize("name", ["", "   "])
    def test_an_empty_meal_name_is_rejected(self, env, name):
        assert create(env, event_type="camp", meals={"options": [{"name": name}]}).status_code == 422

    @pytest.mark.parametrize("option_id", ["", " ", "has space", "-x", "a/b", "x" * 65, 5])
    def test_an_empty_or_unsafe_meal_id_is_rejected(self, env, option_id):
        assert create(env, event_type="camp", meals={"options": [{"id": option_id, "name": "Lunch"}]}).status_code == 422

    def test_an_explicit_null_id_means_generate_one(self, env):
        data = create(env, event_type="camp", meals={"options": [{"id": None, "name": "Lunch"}]}).json()
        assert len(data["meals"]["options"][0]["id"]) == 36

    def test_meals_are_visible_on_the_public_detail_and_list(self, env):
        event = meal_event(env)
        anonymous = client_for(env.db, None)
        anonymous.app  # noqa: B018  (client exists; auth is optional on these reads)
        detail = client_for(env.db, customer_user("c@example.com")).get(f"{API}/{event.id}").json()
        assert detail["meals"]["enabled"] is True and ids(detail) == ["breakfast", "lunch", "dinner"]
        listing = client_for(env.db, customer_user("c@example.com")).get(f"{API}/", params={"page_size": 100}).json()["items"]
        assert next(e for e in listing if e["id"] == str(event.id))["meals"]["enabled"] is True

    def test_duplicating_an_event_copies_the_options_with_their_ids(self, env):
        event = meal_event(env)
        clone = owner_client(env).post(f"{API}/{event.id}/duplicate")
        assert clone.status_code in (200, 201), clone.text
        assert ids(clone.json()) == ["breakfast", "lunch", "dinner"] and clone.json()["id"] != str(event.id)
        assert clone.json()["status"] == "draft"


# ===========================================================================
# Updates: stable ids, retiring, partial-update semantics
# ===========================================================================


class TestUpdates:
    @pytest.fixture
    def event_id(self, env):
        return create(env, event_type="conference", meals={"options": [
            {"id": "breakfast-day-1", "name": "Breakfast"}, {"name": "Lunch"}, {"name": "Dinner"}]}).json()["id"]

    def current_ids(self, env, event_id):
        return ids(owner_client(env).get(f"{API}/{event_id}").json())

    def test_an_unrelated_update_preserves_meals_exactly(self, env, event_id):
        before = row(env, event_id).meals
        for fields in ({"title": "Renamed"}, {"description": "New text"}, {"capacity": "80"}, {"category": "Wellness"}, {"tags": ["a"]}):
            assert update(env, event_id, **fields).status_code == 200
            assert row(env, event_id).meals == before
        assert update(env, event_id, title="Again").json()["meals"]["options"][0]["id"] == "breakfast-day-1"

    def test_a_modules_update_preserves_meals_unless_meals_is_explicitly_changed(self, env, event_id):
        before = row(env, event_id).meals
        assert update(env, event_id, modules={"tickets": True}).status_code == 200
        assert row(env, event_id).meals == before
        off = update(env, event_id, modules={"meals": False}).json()
        assert off["meals"]["enabled"] is False and ids(off) == ids(before["options"])  # switched off, configuration kept
        assert row(env, event_id).meals == before
        on = update(env, event_id, modules={"meals": True}).json()
        assert on["meals"]["enabled"] is True and ids(on) == ids(before["options"])  # ... and it comes back exactly as it was

    def test_ids_are_stable_across_repeated_updates(self, env, event_id):
        original = self.current_ids(env, event_id)
        for _ in range(3):
            listed = owner_client(env).get(f"{API}/{event_id}").json()["meals"]["options"]
            assert update(env, event_id, meals={"options": listed}).status_code == 200
            assert self.current_ids(env, event_id) == original

    def test_options_sent_without_ids_reuse_the_existing_ids_by_name(self, env, event_id):
        original = self.current_ids(env, event_id)
        resp = update(env, event_id, meals={"options": [{"name": "Dinner"}, {"name": "breakfast"}, {"name": "LUNCH"}]})
        assert resp.status_code == 200, resp.text
        assert ids(resp.json()) == [original[2], original[0], original[1]]  # reordered, identities kept, nothing regenerated

    def test_a_rename_keeps_the_id(self, env, event_id):
        original = self.current_ids(env, event_id)
        resp = update(env, event_id, meals={"options": [{"id": original[0], "name": "Brunch"}, {"id": original[1], "name": "Lunch"}, {"id": original[2], "name": "Dinner"}]})
        assert [(o["id"], o["name"]) for o in resp.json()["meals"]["options"]][0] == (original[0], "Brunch")

    def test_a_new_option_gets_a_new_id_and_the_others_keep_theirs(self, env, event_id):
        original = self.current_ids(env, event_id)
        listed = owner_client(env).get(f"{API}/{event_id}").json()["meals"]["options"]
        resp = update(env, event_id, meals={"options": listed + [{"name": "Snacks"}]})
        after = ids(resp.json())
        assert after[:3] == original and len(after) == 4 and after[3] not in original

    def test_options_an_update_no_longer_lists_are_retired_not_deleted(self, env, event_id):
        original = self.current_ids(env, event_id)
        resp = update(env, event_id, meals={"options": [{"id": original[0], "name": "Breakfast"}, {"id": original[1], "name": "Lunch"}]})
        options = resp.json()["meals"]["options"]
        assert [(o["id"], o["active"]) for o in options] == [(original[0], True), (original[1], True), (original[2], False)]
        assert options[2]["name"] == "Dinner"
        assert len(row(env, event_id).meals["options"]) == 3  # nothing was deleted from storage

    def test_a_retired_option_can_be_brought_back_with_its_id(self, env, event_id):
        original = self.current_ids(env, event_id)
        update(env, event_id, meals={"options": [{"id": original[0], "name": "Breakfast"}]})
        back = update(env, event_id, meals={"options": [{"name": "Lunch"}, {"name": "Breakfast", "id": original[0]}, {"id": original[2], "name": "Dinner"}]})
        assert {o["id"]: o["active"] for o in back.json()["meals"]["options"]} == {original[0]: True, original[1]: True, original[2]: True}

    def test_an_empty_options_list_retires_everything_but_keeps_it_all(self, env, event_id):
        original = self.current_ids(env, event_id)
        resp = update(env, event_id, meals={"options": []})
        assert [o["active"] for o in resp.json()["meals"]["options"]] == [False, False, False]
        assert ids(resp.json()) == original

    def test_meals_null_or_without_options_is_no_change(self, env, event_id):
        before = row(env, event_id).meals
        assert update(env, event_id, meals=None).status_code == 200
        assert update(env, event_id, meals={}).status_code == 200
        assert update(env, event_id, meals={"enabled": True}).status_code == 200
        assert row(env, event_id).meals == before

    def test_an_edit_form_that_echoes_the_event_back_changes_nothing(self, env, event_id):
        update(env, event_id, meals={"options": [{"id": "breakfast-day-1", "name": "Breakfast"}]})  # so one option is retired
        before = row(env, event_id).meals
        echoed = owner_client(env).get(f"{API}/{event_id}").json()["meals"]  # {"enabled", "options": [..., "active"]}
        resp = update(env, event_id, meals=echoed)
        assert resp.status_code == 200, resp.text
        assert row(env, event_id).meals == before

    def test_echoing_a_disabled_event_back_is_still_fine_but_changing_it_is_not(self, env, event_id):
        update(env, event_id, modules={"meals": False})
        echoed = owner_client(env).get(f"{API}/{event_id}").json()["meals"]
        assert echoed["enabled"] is False and echoed["options"]
        assert update(env, event_id, meals=echoed).status_code == 200
        blocked = update(env, event_id, meals={"options": [{"name": "Extra"}]})
        assert blocked.status_code == 422 and "Meals are disabled" in blocked.json()["detail"]

    def test_the_enabled_mirror_must_agree_on_update_too(self, env, event_id):
        resp = update(env, event_id, meals={"enabled": False, "options": []})
        assert resp.status_code == 422 and "conflicts with modules.meals" in resp.json()["detail"]
        assert update(env, event_id, modules={"meals": False}, meals={"enabled": False}).status_code == 200

    def test_meals_are_judged_against_the_modules_of_the_same_request(self, env):
        event = create(env, event_type="workshop").json()  # meals off by default
        assert update(env, event["id"], meals={"options": [{"name": "Lunch"}]}).status_code == 422
        both = update(env, event["id"], modules={"meals": True}, meals={"options": [{"name": "Lunch"}]})
        assert both.status_code == 200 and both.json()["meals"]["enabled"] is True and len(both.json()["meals"]["options"]) == 1

    def test_a_failed_update_changes_nothing(self, env, event_id):
        before = snapshot(env)
        for bad in ({"options": [{"name": ""}]}, {"options": [{"name": "A"}, {"name": "a"}]}, {"options": [{"name": "A", "price": 1}]}):
            assert update(env, event_id, title="Should not stick", meals=bad).status_code == 422
        assert snapshot(env) == before

    def test_options_cannot_be_configured_on_an_event_that_never_had_meals(self, env):
        legacy = create(env).json()
        resp = update(env, legacy["id"], meals={"options": [{"name": "Lunch"}]})
        assert resp.status_code == 422 and "modules.meals" in resp.json()["detail"]
        assert row(env, legacy["id"]).meals is None and row(env, legacy["id"]).modules is None

    def test_updating_with_no_meals_field_never_touches_a_legacy_event(self, env):
        legacy = create(env).json()
        before = snapshot(env)
        assert update(env, legacy["id"], title="Renamed").status_code == 200
        env.db.expire_all()
        assert (row(env, legacy["id"]).meals, row(env, legacy["id"]).modules) == (None, None)
        assert before[1] != snapshot(env)[1]  # (the title changed, that is all)


# ===========================================================================
# 11-12. Legacy events
# ===========================================================================


class TestLegacy:
    @pytest.fixture
    def legacy(self, env):
        event = make_event(env.db, env.tenant, env.ent, status="published", capacity="50")
        assert event.modules is None and event.meals is None
        return event

    def test_a_legacy_event_does_not_suddenly_gain_meals(self, env, legacy):
        detail = client_for(env.db, customer_user("c@example.com")).get(f"{API}/{legacy.id}").json()
        assert detail["meals"] == {"enabled": False, "options": []} and detail["modules"]["meals"] is False
        assert dashboard(env, legacy)["meals"] == []

    def test_selections_are_refused_on_a_legacy_event_and_empty_ones_are_fine(self, env, legacy):
        assert register(env, legacy, "a@example.com", ["breakfast"]).status_code == 422
        assert register(env, legacy, "b@example.com", []).status_code == 201
        assert register(env, legacy, "c@example.com").status_code == 201
        assert all(r.meal_selections is None for r in env.db.query(EventRegistration))  # [] stores nothing

    def test_no_option_is_ever_invented(self, env, legacy):
        register(env, legacy, "a@example.com")
        item = owner_client(env).get(f"{API}/{legacy.id}/attendees").json()["items"][0]
        assert item["meal_selections"] == []
        assert row(env, legacy.id).meals is None

    def test_reading_a_legacy_event_performs_no_database_write(self, env, legacy):
        register(env, legacy, "a@example.com")
        registration = env.db.query(EventRegistration).one()
        before = (row(env, legacy.id).meals, row(env, legacy.id).modules, row(env, legacy.id).updated_at, registration.meal_selections)
        customer = client_for(env.db, customer_user("a@example.com"))
        owner = owner_client(env)
        with Statements(env.db) as statements:
            assert customer.get(f"{API}/{legacy.id}").status_code == 200
            assert customer.get(f"{API}/", params={"page_size": 100}).status_code == 200
            assert owner.get(f"{API}/{legacy.id}/attendees").status_code == 200
            assert owner.get(f"{API}/{legacy.id}/registrations/{registration.id}").status_code == 200
            assert owner.get(f"{API}/{legacy.id}/dashboard").status_code == 200
            assert owner.get(f"{API}/{legacy.id}/registrations/export").status_code == 200
        assert statements.writes == []
        env.db.expire_all()
        assert (row(env, legacy.id).meals, row(env, legacy.id).modules, row(env, legacy.id).updated_at,
                env.db.query(EventRegistration).one().meal_selections) == before

    def test_existing_registrations_of_a_legacy_event_are_never_modified(self, env, legacy):
        registration = make_registration(env.db, legacy, "old@example.com")
        update(env, legacy.id, title="Edited")
        env.db.expire_all()
        assert env.db.get(EventRegistration, registration.id).meal_selections is None

    def test_an_event_configured_before_meal_options_existed_is_valid(self, env):
        event = make_event(env.db, env.tenant, env.ent, status="published", modules=MEALS_ON)  # Phase 2.2 type default, meals=NULL
        assert client_for(env.db, customer_user("c@example.com")).get(f"{API}/{event.id}").json()["meals"] == {"enabled": True, "options": []}
        assert register(env, event, "a@example.com", ["lunch"]).status_code == 422  # nothing configured: nothing selectable
        assert register(env, event, "b@example.com").status_code == 201


# ===========================================================================
# 13-19. Online registration
# ===========================================================================


class TestRegistration:
    def test_valid_selections_are_accepted_and_stored_in_option_order(self, env):
        event = meal_event(env)
        resp = register(env, event, "a@example.com", ["dinner", "breakfast"])
        assert resp.status_code == 201, resp.text
        assert resp.json()["meal_selections"] == ["breakfast", "dinner"]
        assert stored_selections(env, resp.json()["id"]) == ["breakfast", "dinner"]

    def test_all_options_can_be_selected(self, env):
        event = meal_event(env)
        assert register(env, event, "a@example.com", ["breakfast", "lunch", "dinner"]).status_code == 201

    def test_an_unknown_meal_is_rejected_and_no_registration_is_created(self, env):
        event = meal_event(env)
        before = snapshot(env)
        resp = register(env, event, "a@example.com", ["breakfast", "brunch"])
        assert resp.status_code == 422 and "Unknown meal option(s) for this event: brunch" in resp.json()["detail"]
        assert snapshot(env) == before

    def test_a_meal_the_event_does_not_have_cannot_be_submitted(self, env):
        event = meal_event(env, options=[option("breakfast", "Breakfast")])
        resp = register(env, event, "a@example.com", ["lunch"])
        assert resp.status_code == 422 and env.db.query(EventRegistration).count() == 0

    def test_meals_disabled_rejects_selections(self, env):
        event = meal_event(env, modules=ALL_OFF)  # options exist but the module is off
        resp = register(env, event, "a@example.com", ["breakfast"])
        assert resp.status_code == 422 and "not enabled" in resp.json()["detail"]
        assert register(env, event, "b@example.com", []).status_code == 201

    def test_duplicate_selections_are_rejected(self, env):
        event = meal_event(env)
        resp = register(env, event, "a@example.com", ["lunch", "lunch"])
        assert resp.status_code == 422 and "duplicate meal selection: lunch" in resp.text
        assert env.db.query(EventRegistration).count() == 0

    @pytest.mark.parametrize("bad", ["lunch", 5, {"lunch": True}, [None], [5], [["lunch"]], [{"id": "lunch"}], ["bad id"], [""], ["x"] * 101])
    def test_malformed_selections_are_rejected(self, env, bad):
        event = meal_event(env)
        resp = client_for(env.db, customer_user("a@example.com")).post(
            f"{API}/{event.id}/registrations", json={"participant_name": "A", "participant_email": "a@example.com", "meal_selections": bad})
        assert resp.status_code == 422
        assert env.db.query(EventRegistration).count() == 0

    def test_a_retired_option_cannot_be_selected(self, env):
        event = meal_event(env, options=[option("breakfast", "Breakfast"), option("lunch", "Lunch", active=False)])
        resp = register(env, event, "a@example.com", ["lunch"])
        assert resp.status_code == 422 and "no longer available: Lunch" in resp.json()["detail"]

    def test_registration_without_selections_works_everywhere(self, env):
        event = meal_event(env)
        resp = register(env, event, "a@example.com")
        assert resp.status_code == 201 and resp.json()["meal_selections"] is None and stored_selections(env, resp.json()["id"]) is None
        assert register(env, event, "b@example.com", []).status_code == 201

    def test_a_non_meal_event_registers_exactly_as_before(self, env):
        event = make_event(env.db, env.tenant, env.ent, status="published", capacity="5")
        data = register(env, event, "a@example.com").json()
        assert data["status"] == "confirmed" and data["qr_code"] and data["participant_email"] == "a@example.com"
        assert {"id", "event_id", "participant_name", "participant_email", "custom_fields", "ticket_type_id", "status", "qr_code",
                "checked_in_at", "checked_in_by", "checked_out_at", "session_id", "registration_source", "created_at"} <= set(data)
        assert data["meal_selections"] is None

    def test_group_members_do_not_inherit_the_registrants_meals(self, env):
        event = meal_event(env)
        resp = register(env, event, "lead@example.com", ["lunch"], group_size=2, group_members=[{"name": "M", "email": "m@example.com"}])
        assert resp.status_code == 201
        env.db.expire_all()
        by_email = {r.participant_email: r.meal_selections for r in env.db.query(EventRegistration)}
        assert by_email == {"lead@example.com": ["lunch"], "m@example.com": None}

    def test_a_payload_object_without_a_real_selection_list_means_none(self, env):
        """Older callers and unit tests hand the service a bare payload object; only a real list is a selection."""
        from unittest.mock import MagicMock

        from app.services.event_service import create_registration_service

        event = meal_event(env, modules=ALL_OFF)  # meals off: a MagicMock mistaken for a selection would be refused
        for meals in (MagicMock(), None, "lunch", 5, {"lunch": True}):
            payload = SimpleNamespace(participant_name="A", participant_email=f"{uuid4().hex[:6]}@example.com", custom_fields=None,
                                      ticket_type_id=None, group_size=None, group_members=None, meal_selections=meals)
            created = create_registration_service(env.db, event.id, payload)
            assert created.meal_selections is None

    def test_paid_checkout_is_untouched_and_meals_are_chosen_afterwards(self, env):
        paid = make_event(env.db, env.tenant, env.ent, status="published", pricing_type="paid", price="500", modules={**MEALS_ON, "tickets": True},
                          meals={"options": [dict(o) for o in THREE]}, ticket_types=[paid_ticket("500", ticket_id="general")], capacity="10")
        checkout = client_for(env.db, customer_user("buyer@example.com")).post(f"{API}/{paid.id}/checkout", json={
            "participant_name": "Buyer", "participant_email": "buyer@example.com", "ticket_type_id": "general", "quantity": 1})
        assert checkout.status_code == 201, checkout.text
        registration = env.db.query(EventRegistration).one()
        assert registration.meal_selections is None
        order = env.db.query(EventOrder).one()
        assert (order.status, order.payment_status, order.amount) == ("confirmed", "confirmed", "500.0")
        buyer = client_for(env.db, customer_user("buyer@example.com"))
        assert patch_meals(env, paid, registration.id, ["lunch"], client=buyer).status_code == 200
        assert stored_selections(env, registration.id) == ["lunch"]

    def test_the_registration_window_and_capacity_rules_still_apply_first(self, env):
        event = meal_event(env, registration_close_at=datetime.utcnow() - timedelta(days=1))
        assert register(env, event, "a@example.com", ["lunch"]).status_code == 400


# ===========================================================================
# 20-22. Updating selections after registration
# ===========================================================================


class TestUpdateEndpoint:
    @pytest.fixture
    def world(self, env):
        event = meal_event(env)
        mine = make_registration(env.db, event, "me@example.com", meal_selections=["breakfast"])
        theirs = make_registration(env.db, event, "them@example.com", meal_selections=["dinner"])
        return SimpleNamespace(event=event, mine=mine, theirs=theirs, me=client_for(env.db, customer_user("me@example.com")))

    def test_a_customer_can_update_their_own_selections(self, env, world):
        resp = patch_meals(env, world.event, world.mine.id, ["dinner", "lunch"], client=world.me)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "event_id": str(world.event.id), "registration_id": str(world.mine.id),
            "meal_selections": [{"meal_id": "lunch", "name": "Lunch", "active": True}, {"meal_id": "dinner", "name": "Dinner", "active": True}]}
        assert stored_selections(env, world.mine.id) == ["lunch", "dinner"]

    def test_the_selection_is_replaced_and_an_empty_list_clears_it(self, env, world):
        assert patch_meals(env, world.event, world.mine.id, [], client=world.me).json()["meal_selections"] == []
        assert stored_selections(env, world.mine.id) is None

    def test_the_email_match_ignores_case_and_whitespace(self, env, world):
        shouting = client_for(env.db, customer_user("  ME@Example.COM "))
        assert patch_meals(env, world.event, world.mine.id, ["lunch"], client=shouting).status_code == 200

    def test_a_stored_mixed_case_email_still_identifies_the_participant(self, env, world):
        """Older rows may hold the email with capitals; identity is compared on lower(email) on BOTH sides."""
        env.db.query(EventRegistration).filter_by(id=world.mine.id).update({"participant_email": "  Me@Example.COM "})
        env.db.commit()
        assert patch_meals(env, world.event, world.mine.id, ["lunch"], client=world.me).status_code == 200

    def test_a_customer_cannot_update_someone_elses_registration(self, env, world):
        before = snapshot(env)
        resp = patch_meals(env, world.event, world.theirs.id, ["breakfast"], client=world.me)
        assert resp.status_code == 403
        assert snapshot(env) == before

    def test_knowing_a_registration_id_grants_nothing(self, env, world):
        stranger = client_for(env.db, customer_user("stranger@example.com"))
        assert patch_meals(env, world.event, world.mine.id, [], client=stranger).status_code == 403
        assert client_for(env.db, None).patch(f"{API}/{world.event.id}/registrations/{world.mine.id}/meals", json={"meal_selections": []}).status_code == 401
        assert stored_selections(env, world.mine.id) == ["breakfast"]

    def test_a_registration_of_another_event_is_rejected(self, env, world):
        other = meal_event(env)
        foreign = make_registration(env.db, other, "me@example.com", meal_selections=["lunch"])  # the same person, another event
        resp = patch_meals(env, world.event, foreign.id, ["breakfast"], client=world.me)
        assert resp.status_code == 404 and stored_selections(env, foreign.id) == ["lunch"]
        # ... and the path event must be the registration's: the participant cannot use event B's URL for event A's registration
        assert patch_meals(env, other, world.mine.id, ["breakfast"], client=world.me).status_code == 404
        assert stored_selections(env, world.mine.id) == ["breakfast"]

    def test_unknown_registration_and_unknown_event_are_404(self, env, world):
        assert patch_meals(env, world.event, uuid4(), [], client=world.me).status_code == 404
        assert world.me.patch(f"{API}/{uuid4()}/registrations/{world.mine.id}/meals", json={"meal_selections": []}).status_code == 404

    def test_validation_is_the_shared_validator(self, env, world):
        for selections, fragment in ((["brunch"], "Unknown meal option"),):
            resp = patch_meals(env, world.event, world.mine.id, selections, client=world.me)
            assert resp.status_code == 422 and fragment in resp.json()["detail"]
        for malformed in (["lunch", "lunch"], ["bad id"], [None], "lunch", None):
            assert world.me.patch(f"{API}/{world.event.id}/registrations/{world.mine.id}/meals", json={"meal_selections": malformed}).status_code == 422
        assert stored_selections(env, world.mine.id) == ["breakfast"]

    def test_a_retired_option_can_be_kept_but_not_added(self, env, world):
        update(env, world.event.id, meals={"options": [{"id": "lunch", "name": "Lunch"}, {"id": "dinner", "name": "Dinner"}]})  # breakfast retired
        kept = patch_meals(env, world.event, world.mine.id, ["breakfast", "lunch"], client=world.me)
        assert kept.status_code == 200
        # option order: the listed options first, the retired one after them
        assert [(m["meal_id"], m["active"]) for m in kept.json()["meal_selections"]] == [("lunch", True), ("breakfast", False)]
        stranger = patch_meals(env, world.event, world.theirs.id, ["breakfast"], client=client_for(env.db, customer_user("them@example.com")))
        assert stranger.status_code == 422 and "no longer available" in stranger.json()["detail"]

    def test_meals_switched_off_allow_clearing_but_not_selecting(self, env, world):
        update(env, world.event.id, modules={"meals": False})
        assert patch_meals(env, world.event, world.mine.id, ["lunch"], client=world.me).status_code == 422
        assert patch_meals(env, world.event, world.mine.id, [], client=world.me).status_code == 200

    def test_a_cancelled_or_no_show_registration_cannot_change_its_meals(self, env, world):
        for status_ in ("cancelled", "no_show"):
            env.db.query(EventRegistration).filter_by(id=world.mine.id).update({"status": status_})
            env.db.commit()
            resp = patch_meals(env, world.event, world.mine.id, ["lunch"], client=world.me)
            assert resp.status_code == 400 and status_ in resp.json()["detail"]

    @pytest.mark.parametrize("event_status", ["cancelled", "completed", "archived", "suspended"])
    def test_a_closed_event_takes_no_meal_changes(self, env, world, event_status):
        env.db.query(Event).filter_by(id=world.event.id).update({"status": event_status})
        env.db.commit()
        resp = patch_meals(env, world.event, world.mine.id, ["lunch"], client=world.me)
        assert resp.status_code == 400 and "closed" in resp.json()["detail"]

    def test_an_attended_participant_can_still_choose(self, env, world):
        env.db.query(EventRegistration).filter_by(id=world.mine.id).update({"status": "attended"})
        env.db.commit()
        assert patch_meals(env, world.event, world.mine.id, ["lunch"], client=world.me).status_code == 200

    def test_nothing_but_the_selection_changes(self, env, world):
        paid = make_event(env.db, env.tenant, env.ent, status="published", pricing_type="paid", price="10", modules=MEALS_ON,
                          meals={"options": [dict(o) for o in THREE]}, capacity="5")
        registration = make_registration(env.db, paid, "me@example.com")
        make_order(env.db, paid, "me@example.com", amount="10", created_at=LONG_AGO)
        make_waitlist(env.db, paid, "queued@example.com", status="waiting")
        before = env.db.query(EventOrder).one().amount, env.db.query(EventWaitlist).one().status, registration.status, paid.capacity
        assert patch_meals(env, paid, registration.id, ["lunch"], client=world.me).status_code == 200
        env.db.expire_all()
        assert (env.db.query(EventOrder).one().amount, env.db.query(EventWaitlist).one().status,
                env.db.get(EventRegistration, registration.id).status, env.db.get(Event, paid.id).capacity) == before
        env.apply_async.assert_not_called()

    def test_the_body_cannot_carry_ownership_or_identity_fields(self, env, world):
        for extra in ({"tenant_id": str(uuid4())}, {"enterprise_id": str(uuid4())}, {"participant_email": "x@example.com"}, {"status": "attended"},
                      {"registration_id": str(world.theirs.id)}, {"event_id": str(uuid4())}):
            resp = world.me.patch(f"{API}/{world.event.id}/registrations/{world.mine.id}/meals", json={"meal_selections": ["lunch"], **extra})
            assert resp.status_code == 422, extra
        assert stored_selections(env, world.mine.id) == ["breakfast"]

    def test_the_route_does_not_shadow_the_existing_registration_routes(self, env, world):
        assert owner_client(env).get(f"{API}/{world.event.id}/registrations/{world.mine.id}").status_code == 200
        assert owner_client(env).get(f"{API}/{world.event.id}/registrations/export").status_code == 200


# ===========================================================================
# 23-25. Walk-in
# ===========================================================================


class TestWalkIn:
    def test_a_walk_in_with_meal_selections(self, env):
        event = meal_event(env)
        resp = walk_in(env, event, "w@example.com", ["dinner", "lunch"])
        assert resp.status_code == 201, resp.text
        assert [m["meal_id"] for m in resp.json()["registration"]["meal_selections"]] == ["lunch", "dinner"]
        (registration,) = env.db.query(EventRegistration).all()
        assert registration.meal_selections == ["lunch", "dinner"] and registration.registration_source == "walk_in"
        assert audits(env, "walk_in_registration")[0].after["meal_selections"] == ["lunch", "dinner"]

    def test_an_invalid_meal_in_a_walk_in_is_rejected_and_nothing_is_created(self, env):
        event = meal_event(env)
        before = snapshot(env)
        for selections, code in ((["brunch"], 422), (["lunch", "lunch"], 422), ([None], 422), (["bad id"], 422)):
            assert walk_in(env, event, "w@example.com", selections).status_code == code
        assert snapshot(env) == before

    def test_meals_off_rejects_a_walk_ins_selections(self, env):
        event = meal_event(env, modules=ALL_OFF)
        resp = walk_in(env, event, "w@example.com", ["lunch"])
        assert resp.status_code == 422 and "not enabled" in resp.json()["detail"]
        assert env.db.query(EventRegistration).count() == 0

    def test_a_walk_in_without_meals_is_unchanged(self, env):
        event = meal_event(env)
        resp = walk_in(env, event, "w@example.com")
        assert resp.status_code == 201 and resp.json()["registration"]["meal_selections"] == []
        assert audits(env, "walk_in_registration")[0].after["meal_selections"] == []
        plain = make_event(env.db, env.tenant, env.ent, status="published")
        assert walk_in(env, plain, "p@example.com").status_code == 201

    def test_the_walk_in_uses_the_same_validator_as_online_registration(self, env, monkeypatch):
        assert walk_in_service.validated_selections is meal_service.validated_selections
        calls = []
        real = walk_in_service.validated_selections
        monkeypatch.setattr(walk_in_service, "validated_selections", lambda *a, **k: (calls.append(a[1]), real(*a, **k))[1])
        walk_in(env, meal_event(env), "w@example.com", ["lunch"])
        assert calls == [["lunch"]]

    def test_a_failed_walk_in_leaves_no_audit_and_no_registration(self, env, monkeypatch):
        event = meal_event(env)

        def boom():
            env.db.flush()
            raise RuntimeError("database went away")

        env.db.commit = boom
        try:
            resp = walk_in(env, event, "w@example.com", ["lunch"])
        finally:
            del env.db.commit
        assert resp.status_code == 500
        assert env.db.query(EventRegistration).count() == 0 and audits(env) == []

    def test_a_walk_in_can_check_in_and_pick_meals_together(self, env):
        event = meal_event(env)
        resp = owner_client(env).post(f"{API}/{event.id}/walk-in", json={
            "participant_name": "W", "participant_email": "w@example.com", "meal_selections": ["breakfast"], "check_in": True})
        assert resp.status_code == 201 and resp.json()["registration"]["is_checked_in"] is True


# ===========================================================================
# 26-28. Attendee management + export
# ===========================================================================


class TestAttendees:
    @pytest.fixture
    def world(self, env):
        event = meal_event(env)
        make_registration(env.db, event, "a@example.com", meal_selections=["breakfast", "dinner"])
        make_registration(env.db, event, "b@example.com")
        return event

    def listing(self, env, event, **params):
        resp = owner_client(env).get(f"{API}/{event.id}/attendees", params={"page_size": 100, **params})
        assert resp.status_code == 200, resp.text
        return {i["participant_email"]: i for i in resp.json()["items"]}

    def test_the_attendee_response_includes_meal_selections(self, env, world):
        items = self.listing(env, world)
        assert items["a@example.com"]["meal_selections"] == [
            {"meal_id": "breakfast", "name": "Breakfast", "active": True}, {"meal_id": "dinner", "name": "Dinner", "active": True}]
        assert items["b@example.com"]["meal_selections"] == []

    def test_the_detail_matches_the_list(self, env, world):
        item = self.listing(env, world)["a@example.com"]
        detail = owner_client(env).get(f"{API}/{world.id}/registrations/{item['registration_id']}").json()
        assert detail == item

    def test_selections_follow_the_events_option_order_and_show_retired_options(self, env, world):
        update(env, world.id, meals={"options": [{"id": "dinner", "name": "Dinner"}, {"id": "lunch", "name": "Lunch"}]})  # breakfast retired, reordered
        item = self.listing(env, world)["a@example.com"]
        assert [(m["meal_id"], m["active"]) for m in item["meal_selections"]] == [("dinner", True), ("breakfast", False)]

    def test_an_id_the_configuration_does_not_know_is_shown_not_hidden(self, env, world):
        env.db.query(EventRegistration).filter_by(participant_email="b@example.com").update({"meal_selections": ["ghost"]})
        env.db.commit()
        assert self.listing(env, world)["b@example.com"]["meal_selections"] == [{"meal_id": "ghost", "name": None, "active": False}]

    def test_no_internal_fields_are_exposed(self, env, world):
        selection = self.listing(env, world)["a@example.com"]["meal_selections"][0]
        assert set(selection) == {"meal_id", "name", "active"}

    def test_the_list_costs_no_extra_query_per_attendee(self, env):
        event = meal_event(env)

        def selects():
            with Statements(env.db) as statements:
                assert owner_client(env).get(f"{API}/{event.id}/attendees", params={"page_size": 100}).status_code == 200
            return statements.selects

        for i in range(3):
            make_registration(env.db, event, f"few{i}@example.com", meal_selections=["lunch"])
        few = selects()
        for i in range(60):
            make_registration(env.db, event, f"many{i}@example.com", meal_selections=["breakfast", "dinner"])
        assert selects() == few

    def test_the_export_has_a_meals_column_after_the_existing_ones(self, env, world):
        rows = list(csv.reader(io.StringIO(owner_client(env).get(f"{API}/{world.id}/registrations/export").text)))
        header = rows[0]
        meals_col = header.index("meals")
        assert header[:5] == ["id", "name", "email", "status", "qr_code"] and header[meals_col - 1] == "source"
        assert {r[2]: r[meals_col] for r in rows[1:]} == {"a@example.com": "Breakfast; Dinner", "b@example.com": ""}

    def test_the_export_lists_names_not_ids_and_keeps_retired_ones(self, env, world):
        update(env, world.id, meals={"options": [{"id": "dinner", "name": "Supper"}]})
        rows = list(csv.reader(io.StringIO(owner_client(env).get(f"{API}/{world.id}/registrations/export").text)))
        meals_col = rows[0].index("meals")
        assert {r[2]: r[meals_col] for r in rows[1:]}["a@example.com"] == "Supper; Breakfast"

    def test_the_export_is_formula_injection_safe(self, env):
        options = [option("f1", "=HYPERLINK(\"http://evil\",\"x\")"), option("f2", "+1+1"), option("f3", "-2+3"), option("f4", "@SUM(A1)"), option("ok", "Lunch")]
        event = meal_event(env, options=options)
        make_registration(env.db, event, "a@example.com", meal_selections=["f1"])
        make_registration(env.db, event, "b@example.com", meal_selections=["f2", "f3"])
        make_registration(env.db, event, "c@example.com", meal_selections=["f4"])
        make_registration(env.db, event, "d@example.com", meal_selections=["ok", "f1"])
        csv_rows = list(csv.reader(io.StringIO(owner_client(env).get(f"{API}/{event.id}/registrations/export").text)))
        meals_col = csv_rows[0].index("meals")
        rows = {r[2]: r[meals_col] for r in csv_rows[1:]}
        assert rows["a@example.com"].startswith("'=HYPERLINK")
        assert rows["b@example.com"] == "'+1+1; -2+3" and rows["c@example.com"] == "'@SUM(A1)"
        assert rows["d@example.com"] == "'=HYPERLINK(\"http://evil\",\"x\"); Lunch"  # option order; only the START of the cell is escaped

    def test_the_export_filters_still_apply(self, env, world):
        rows = list(csv.reader(io.StringIO(owner_client(env).get(f"{API}/{world.id}/registrations/export", params={"q": "a@"}).text)))
        assert [r[2] for r in rows[1:]] == ["a@example.com"]

    def test_meal_names_are_data_not_markup(self, env):
        event = meal_event(env, options=[option("x", "<script>alert(1)</script>")])
        make_registration(env.db, event, "a@example.com", meal_selections=["x"])
        assert owner_client(env).get(f"{API}/{event.id}/attendees").json()["items"][0]["meal_selections"][0]["name"] == "<script>alert(1)</script>"


# ===========================================================================
# 29-33. Dashboard counts
# ===========================================================================


class TestDashboard:
    def test_meal_counts(self, env):
        event = meal_event(env)
        for i in range(3):
            make_registration(env.db, event, f"b{i}@example.com", meal_selections=["breakfast"])
        make_registration(env.db, event, "l@example.com", meal_selections=["lunch"])
        assert dashboard(env, event)["meals"] == [
            {"meal_id": "breakfast", "name": "Breakfast", "selected_count": 3, "active": True},
            {"meal_id": "lunch", "name": "Lunch", "selected_count": 1, "active": True},
            {"meal_id": "dinner", "name": "Dinner", "selected_count": 0, "active": True}]

    def test_one_attendee_can_select_several_meals(self, env):
        event = meal_event(env)
        make_registration(env.db, event, "a@example.com", meal_selections=["breakfast", "lunch", "dinner"])
        make_registration(env.db, event, "b@example.com", meal_selections=["lunch", "dinner"])
        assert meal_counts(env, event) == {"breakfast": 1, "lunch": 2, "dinner": 2}

    def test_zero_selections(self, env):
        event = meal_event(env)
        make_registration(env.db, event, "a@example.com")
        assert meal_counts(env, event) == {"breakfast": 0, "lunch": 0, "dinner": 0}
        assert meal_counts(env, meal_event(env)) == {"breakfast": 0, "lunch": 0, "dinner": 0}  # no registrations at all

    def test_many_attendees(self, env):
        event = meal_event(env)
        for i in range(120):
            make_registration(env.db, event, f"p{i}@example.com", meal_selections=[m for m, keep in (("breakfast", i % 2 == 0), ("lunch", True), ("dinner", i % 3 == 0)) if keep])
        assert meal_counts(env, event) == {"breakfast": 60, "lunch": 120, "dinner": 40}

    def test_only_active_registrations_are_counted(self, env):
        event = meal_event(env)
        make_registration(env.db, event, "ok@example.com", meal_selections=["lunch"])
        make_registration(env.db, event, "in@example.com", status="attended", meal_selections=["lunch"])
        make_registration(env.db, event, "gone@example.com", status="cancelled", meal_selections=["lunch"])
        make_registration(env.db, event, "away@example.com", status="no_show", meal_selections=["lunch"])
        assert meal_counts(env, event)["lunch"] == 2

    def test_a_cancelled_registration_stops_counting(self, env):
        event = meal_event(env)
        registration = make_registration(env.db, event, "a@example.com", meal_selections=["dinner"])
        assert meal_counts(env, event)["dinner"] == 1
        assert owner_client(env).delete(f"{API}/{event.id}/registrations/{registration.id}").status_code == 200
        assert meal_counts(env, event)["dinner"] == 0

    def test_a_refunded_registration_stops_counting(self, env):
        event = make_event(env.db, env.tenant, env.ent, status="published", pricing_type="paid", price="10", modules={**MEALS_ON, "tickets": True},
                           meals={"options": [dict(o) for o in THREE]}, capacity="10")
        make_registration(env.db, event, "keep@example.com", meal_selections=["lunch"])
        refunded = make_registration(env.db, event, "refund@example.com", meal_selections=["lunch"])
        order = make_order(env.db, event, "refund@example.com", amount="10", status="refund_requested", payment_status="refund_requested", created_at=LONG_AGO)
        assert meal_counts(env, event)["lunch"] == 2
        approve = owner_client(env).post(f"{API}/{event.id}/orders/{order.id}/refund/approve", json={"action": "approve"})
        assert approve.status_code == 200, approve.text
        env.db.expire_all()
        assert env.db.get(EventRegistration, refunded.id).status == "cancelled"
        assert meal_counts(env, event)["lunch"] == 1  # the refund cancelled the registration, so it no longer counts

    def test_retired_options_are_listed_only_while_someone_holds_them(self, env):
        event = meal_event(env)
        holder = make_registration(env.db, event, "a@example.com", meal_selections=["dinner"])
        update(env, event.id, meals={"options": [{"id": "breakfast", "name": "Breakfast"}, {"id": "lunch", "name": "Lunch"}]})
        listed = {m["meal_id"]: (m["selected_count"], m["active"]) for m in dashboard(env, event)["meals"]}
        assert listed == {"breakfast": (0, True), "lunch": (0, True), "dinner": (1, False)}
        patch_meals(env, event, holder.id, [])
        assert set(meal_counts(env, event)) == {"breakfast", "lunch"}

    def test_meals_off_or_no_options_gives_an_empty_section_without_a_query(self, env):
        off = meal_event(env, modules=ALL_OFF)
        make_registration(env.db, off, "a@example.com", meal_selections=["lunch"])
        none = meal_event(env, meals=None)
        with Statements(env.db) as statements:
            assert dashboard(env, off)["meals"] == [] and dashboard(env, none)["meals"] == []
        assert statements.selects > 0 and statements.writes == []
        with Statements(env.db) as with_meals:
            dashboard(env, meal_event(env))
        assert with_meals.selects == statements.selects / 2 + 1  # one grouped statement for the counts, nothing per registration

    def test_selections_of_other_events_and_unknown_ids_are_not_counted(self, env):
        event, other = meal_event(env), meal_event(env)
        make_registration(env.db, event, "a@example.com", meal_selections=["lunch", "ghost"])
        make_registration(env.db, other, "a@example.com", meal_selections=["lunch"])
        assert meal_counts(env, event) == {"breakfast": 0, "lunch": 1, "dinner": 0}

    def test_the_counts_are_one_statement_however_many_attendees(self, env):
        event = meal_event(env)

        def selects():
            with Statements(env.db) as statements:
                dashboard(env, event)
            return statements.selects

        for i in range(3):
            make_registration(env.db, event, f"few{i}@example.com", meal_selections=["lunch"])
        few = selects()
        for i in range(80):
            make_registration(env.db, event, f"many{i}@example.com", meal_selections=["breakfast", "lunch", "dinner"])
        assert selects() == few

    def test_every_other_dashboard_number_is_unchanged_by_meals(self, env):
        event = meal_event(env)
        make_registration(env.db, event, "a@example.com")
        make_registration(env.db, event, "b@example.com")
        before = dashboard(env, event)
        for registration in env.db.query(EventRegistration).all():
            registration.meal_selections = ["breakfast", "lunch"]
        env.db.commit()
        after = dashboard(env, event)
        for key in ("event", "registrations", "capacity", "attendance", "waitlist", "orders", "revenue", "sessions"):
            assert after[key] == before[key], key
        assert before["meals"] != after["meals"]

    def test_the_postgres_statement_expands_the_json_array_in_the_database(self):
        from sqlalchemy.dialects import postgresql

        captured = []
        fake = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=postgresql.dialect()),
                               execute=lambda statement: (captured.append(statement), SimpleNamespace(all=lambda: [("lunch", 4)]))[1])
        assert meal_service.selected_meal_counts(fake, uuid4()) == {"lunch": 4}
        sql = str(captured[0].compile(dialect=postgresql.dialect())).replace("\n", " ")
        for fragment in ("jsonb_array_elements_text", "AS anon_1(value)", "jsonb_typeof(event_registrations.meal_selections)", "GROUP BY anon_1.value",
                         "event_registrations.event_id =", "event_registrations.status IN"):
            assert fragment in sql, fragment


# ===========================================================================
# 34-39. Security
# ===========================================================================


class TestSecurity:
    @pytest.fixture
    def world(self, env):
        event = meal_event(env)
        registration = make_registration(env.db, event, "attendee@example.com", meal_selections=["breakfast"])
        return SimpleNamespace(event=event, registration=registration)

    # -- organizer configuration goes through Event create/update (the existing role gate + ownership)

    @pytest.mark.parametrize("role", ["admin", "provider"])
    def test_the_owning_admin_and_provider_can_configure_meals(self, env, world, role):
        resp = client_for(env.db, staff_user(env.tenant, role)).put(f"{API}/{world.event.id}", json={"meals": {"options": [{"id": "lunch", "name": "Lunch"}]}})
        assert resp.status_code == 200, resp.text

    def test_a_foreign_tenant_cannot_configure_meals(self, env, world):
        before = snapshot(env)
        for role in ("admin", "provider"):
            resp = client_for(env.db, staff_user(uuid4(), role)).put(f"{API}/{world.event.id}", json={"meals": {"options": [{"name": "Hack"}]}})
            assert resp.status_code == 403
        assert snapshot(env) == before

    def test_a_customer_cannot_configure_meals(self, env, world):
        resp = client_for(env.db, customer_user("attendee@example.com")).put(f"{API}/{world.event.id}", json={"meals": {"options": []}})
        assert resp.status_code == 403
        assert client_for(env.db, customer_user("c@example.com")).post(f"{API}/", json=body(event_type="camp", meals={"options": [{"name": "A"}]})).status_code == 403

    def test_anonymous_and_inactive_super_admin_are_denied(self, env, world):
        payload = {"meals": {"options": [{"name": "X"}]}}
        assert client_for(env.db, None).put(f"{API}/{world.event.id}", json=payload).status_code == 401
        inactive = {**super_admin_user(), "status": "disabled"}
        assert client_for(env.db, inactive).put(f"{API}/{world.event.id}", json=payload).status_code == 403

    def test_the_platform_super_admin_is_not_admitted_by_the_existing_event_update_gate(self, env, world):
        """Unchanged Phase 2.1 policy: PUT /events/{id} admits admin/provider only. Recorded here so a change is deliberate."""
        assert client_for(env.db, super_admin_user()).put(f"{API}/{world.event.id}", json={"meals": {"options": []}}).status_code == 403

    def test_creating_an_event_needs_an_organizer_and_the_tenant_comes_from_the_session(self, env):
        resp = create(env, event_type="camp", tenant_id=str(uuid4()), meals={"options": [{"name": "A"}]})
        assert resp.status_code == 201
        assert row(env, resp.json()["id"]).tenant_id == env.tenant  # a tenant in the body never wins

    def test_meals_cannot_smuggle_ownership_fields(self, env, world):
        for extra in ({"tenant_id": str(uuid4())}, {"enterprise_id": str(uuid4())}, {"event_id": str(uuid4())}):
            assert update(env, world.event.id, meals={"options": [{"name": "A"}], **extra}).status_code == 422
            assert update(env, world.event.id, meals={"options": [{"name": "A", **extra}]}).status_code == 422

    # -- the organizer's meal update on a registration (ownership)

    @pytest.mark.parametrize("role", ["admin", "provider"])
    def test_the_owning_admin_and_provider_can_change_an_attendees_meals(self, env, world, role):
        resp = patch_meals(env, world.event, world.registration.id, ["lunch"], client=client_for(env.db, staff_user(env.tenant, role)))
        assert resp.status_code == 200, resp.text

    def test_an_active_super_admin_can_change_an_attendees_meals(self, env, world):
        assert patch_meals(env, world.event, world.registration.id, ["lunch"], client=client_for(env.db, super_admin_user())).status_code == 200

    def test_a_foreign_tenant_cannot_change_an_attendees_meals(self, env, world):
        before = snapshot(env)
        for role in ("admin", "provider"):
            assert patch_meals(env, world.event, world.registration.id, ["lunch"], client=client_for(env.db, staff_user(uuid4(), role))).status_code == 403
        assert snapshot(env) == before

    def test_an_inactive_super_admin_and_an_anonymous_caller_are_denied(self, env, world):
        inactive = {**super_admin_user(), "status": "disabled"}
        assert patch_meals(env, world.event, world.registration.id, ["lunch"], client=client_for(env.db, inactive)).status_code == 403
        assert patch_meals(env, world.event, world.registration.id, ["lunch"], client=client_for(env.db, None)).status_code == 401
        assert stored_selections(env, world.registration.id) == ["breakfast"]

    def test_another_customer_is_denied(self, env, world):
        assert patch_meals(env, world.event, world.registration.id, ["lunch"], client=client_for(env.db, customer_user("other@example.com"))).status_code == 403

    def test_a_staff_member_of_another_tenant_who_is_the_participant_may_change_their_own_meals(self, env, world):
        """Identity is the participant's email, as for cancellation and the QR: a provider who registered elsewhere is a participant."""
        outsider = {**staff_user(uuid4(), "provider"), "email": "attendee@example.com"}
        assert patch_meals(env, world.event, world.registration.id, ["lunch"], client=client_for(env.db, outsider)).status_code == 200

    def test_the_tenant_in_the_query_string_is_ignored(self, env, world):
        resp = client_for(env.db, staff_user(uuid4(), "admin")).patch(
            f"{API}/{world.event.id}/registrations/{world.registration.id}/meals?tenant_id={env.tenant}", json={"meal_selections": ["lunch"]})
        assert resp.status_code == 403


# ===========================================================================
# 40-41. Audit + atomicity
# ===========================================================================


class TestAudit:
    def test_creating_an_event_with_meals_is_audited(self, env):
        data = create(env, event_type="camp", meals={"options": [{"id": "lunch", "name": "Lunch"}]}).json()
        (audit,) = audits(env, "create")
        assert audit.event_id == UUID(data["id"]) and audit.changed_by == env.owner["id"]
        assert audit.after["meals"]["options"][0]["id"] == "lunch" and audit.after["modules"]["meals"] is True

    def test_a_meal_configuration_change_is_audited_with_before_and_after(self, env):
        event_id = create(env, event_type="camp", meals={"options": [{"id": "lunch", "name": "Lunch"}]}).json()["id"]
        update(env, event_id, meals={"options": [{"id": "lunch", "name": "Lunch"}, {"id": "dinner", "name": "Dinner"}]})
        audit = audits(env, "update")[-1]
        assert [o["id"] for o in audit.before["meals"]["options"]] == ["lunch"]
        assert [o["id"] for o in audit.after["meals"]["options"]] == ["lunch", "dinner"]
        assert audit.changed_by == env.owner["id"]

    def test_an_unrelated_update_does_not_put_meals_in_the_audit(self, env):
        event_id = create(env, event_type="camp", meals={"options": [{"name": "Lunch"}]}).json()["id"]
        update(env, event_id, title="Renamed")
        audit = audits(env, "update")[-1]
        assert "meals" not in audit.before and "meals" not in audit.after

    def test_a_modules_change_is_audited_as_before(self, env):
        event_id = create(env, event_type="camp", meals={"options": [{"name": "Lunch"}]}).json()["id"]
        update(env, event_id, modules={"meals": False})
        audit = audits(env, "update")[-1]
        assert audit.before["modules"]["meals"] is True and audit.after["modules"]["meals"] is False and "meals" not in audit.after

    def test_an_organizer_changing_an_attendees_meals_is_audited(self, env):
        event = meal_event(env)
        registration = make_registration(env.db, event, "a@example.com", meal_selections=["breakfast"])
        patch_meals(env, event, registration.id, ["lunch", "dinner"])
        (audit,) = audits(env, "meal_selection_update")
        assert audit.event_id == event.id and audit.changed_by == env.owner["id"]
        assert audit.before == {"registration_id": str(registration.id), "meal_selections": ["breakfast"]}
        assert audit.after == {"registration_id": str(registration.id), "participant_email": "a@example.com", "meal_selections": ["lunch", "dinner"]}

    def test_a_participants_own_change_is_not_audited(self, env):
        event = meal_event(env)
        registration = make_registration(env.db, event, "a@example.com")
        assert patch_meals(env, event, registration.id, ["lunch"], client=client_for(env.db, customer_user("a@example.com"))).status_code == 200
        assert audits(env, "meal_selection_update") == []

    def test_online_registration_with_meals_writes_no_extra_audit(self, env):
        event = meal_event(env)
        register(env, event, "a@example.com", ["lunch"])
        assert audits(env) == []  # as before: online registration has never been audited

    def test_a_failed_commit_leaves_the_selection_and_no_orphan_audit(self, env):
        event = meal_event(env)
        registration = make_registration(env.db, event, "a@example.com", meal_selections=["breakfast"])

        def boom():
            env.db.flush()  # the change and its audit row are really in the transaction when the commit fails
            raise RuntimeError("database went away")

        env.db.commit = boom
        try:
            resp = patch_meals(env, event, registration.id, ["dinner"])
        finally:
            del env.db.commit
        assert resp.status_code == 500
        assert stored_selections(env, registration.id) == ["breakfast"] and audits(env) == []

    def test_a_rejected_meal_change_writes_no_audit(self, env):
        event = meal_event(env)
        registration = make_registration(env.db, event, "a@example.com")
        assert patch_meals(env, event, registration.id, ["brunch"]).status_code == 422
        assert audits(env) == []

    def test_a_failed_event_update_with_meals_leaves_no_audit_and_no_change(self, env):
        event_id = create(env, event_type="camp", meals={"options": [{"name": "Lunch"}]}).json()["id"]
        before = (row(env, event_id).meals, row(env, event_id).title, len(audits(env)))
        assert update(env, event_id, title="Nope", meals={"options": [{"name": ""}]}).status_code == 422
        assert (row(env, event_id).meals, row(env, event_id).title, len(audits(env))) == before

    def test_a_failed_event_create_with_meals_leaves_nothing(self, env):
        assert create(env, event_type="workshop", meals={"options": [{"name": "Lunch"}]}).status_code == 422
        assert env.db.query(Event).count() == 0 and audits(env) == []

    def test_a_failed_event_update_commit_rolls_back_the_meals_and_the_audit(self, env):
        event_id = create(env, event_type="camp", meals={"options": [{"id": "lunch", "name": "Lunch"}]}).json()["id"]
        before = row(env, event_id).meals

        def boom():
            env.db.flush()
            raise RuntimeError("database went away")

        env.db.commit = boom
        try:
            resp = update(env, event_id, meals={"options": [{"id": "lunch", "name": "Lunch"}, {"name": "Dinner"}]})
        finally:
            del env.db.commit
        assert resp.status_code == 500
        env.db.rollback()  # what get_db's session close does at the end of a real request (the harness shares one session)
        assert row(env, event_id).meals == before and len(audits(env, "update")) == 0


# ===========================================================================
# OpenAPI
# ===========================================================================


class TestOpenApi:
    @pytest.fixture(scope="class")
    def spec(self):
        from app.main import app as fastapi_app

        return fastapi_app.openapi()

    def schema(self, spec, name):
        return spec["components"]["schemas"][name]

    def test_the_configuration_fields_are_documented_and_typed(self, spec):
        for name in ("EventCreate", "EventUpdate"):
            field = self.schema(spec, name)["properties"]["meals"]
            assert "EventMealsInput" in str(field) and self.schema(spec, name)["properties"]["meals"].get("description")
        assert "EventMeals" in str(self.schema(spec, "EventResponse")["properties"]["meals"])
        meals_input = self.schema(spec, "EventMealsInput")
        assert meals_input["additionalProperties"] is False and set(meals_input["properties"]) == {"enabled", "options"}
        option_schema = self.schema(spec, "MealOptionInput")
        assert option_schema["additionalProperties"] is False and option_schema["required"] == ["name"]
        assert {"id", "name", "description", "date", "active"} == set(option_schema["properties"])
        assert self.schema(spec, "EventMeals")["required"] == ["enabled"] and "active" in self.schema(spec, "MealOption")["required"]

    def test_the_selection_fields_are_documented(self, spec):
        for name in ("EventRegistrationCreate", "EventWalkInRequest"):
            field = self.schema(spec, name)["properties"]["meal_selections"]
            assert field["description"] and "maxItems" in str(field)
        assert "AttendeeMealSelection" in str(self.schema(spec, "EventAttendeeResponse")["properties"]["meal_selections"])
        assert "DashboardMeal" in str(self.schema(spec, "EventDashboardResponse")["properties"]["meals"])
        assert set(self.schema(spec, "DashboardMeal")["properties"]) == {"meal_id", "name", "selected_count", "active"}

    def test_the_new_operation_is_documented(self, spec):
        operation = spec["paths"]["/api/v1/events/{event_id}/registrations/{reg_id}/meals"]["patch"]
        assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/EventMealSelectionUpdate")
        assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/EventMealSelectionResponse")
        assert {"400", "403", "404", "422"} <= set(operation["responses"])
        assert self.schema(spec, "EventMealSelectionUpdate")["additionalProperties"] is False
        assert operation["security"] == spec["paths"]["/api/v1/events/{event_id}/registrations/{reg_id}"]["delete"]["security"]

    def test_checkout_and_the_payment_models_are_untouched(self, spec):
        assert "meal_selections" not in self.schema(spec, "EventCheckoutRequest")["properties"]
        assert not [k for k in self.schema(spec, "EventOrderResponse")["properties"] if "meal" in k]
        assert set(self.schema(spec, "MealOption")["properties"]) == {"id", "name", "description", "date", "active"}  # no price, capacity or vendor

    def test_existing_event_fields_are_all_still_there(self, spec):
        properties = set(self.schema(spec, "EventResponse")["properties"])
        assert {"modules", "event_type", "lifecycle_state", "sessions", "status", "capacity", "ticket_types"} <= properties
