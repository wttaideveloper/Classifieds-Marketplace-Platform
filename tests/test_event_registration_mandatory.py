"""
Product rule: REGISTRATION IS MANDATORY FOR EVERY EVENT — regression tests.

``modules.registration`` can never be disabled:
  - not via Event create/update (explicit override, with or without an event_type on the request);
  - not via an Event Type's default_modules/allowed_modules/required_modules (create, update, or a row
    seeded/edited before this rule existed);
  - not via legacy/behaviour-based module resolution.

Pure unit coverage (app/utils/event_modules.py, app/schemas/event_type_schema.py) complements
tests/test_event_modules_config.py; HTTP-level coverage complements tests/test_event_type_config.py and
tests/test_event_type_integration.py. Letters A-I below map to the product spec's regression checklist.

Run:
    pytest tests/test_event_registration_mandatory.py -v
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from event_sql_support import (
    API,
    client_for,
    make_enterprise,
    make_session,
    reset_overrides,
    silence_side_effects,
    staff_user,
)
from app.utils.event_modules import (
    EventModuleConfigError,
    legacy_modules,
    resolve_event_modules,
    validate_module_overrides,
)

TYPES_API = "/api/v1/event-types"
SPEC_KEYS = ["registration", "tickets", "sessions", "check_in", "online_meeting", "custom_questions", "meals", "accommodation"]


def flags(*enabled):
    return {key: key in enabled for key in SPEC_KEYS}


def detail_mentions(resp, text) -> bool:
    """True if `text` appears in the response's error detail, whichever shape it comes in: a plain
    string (an explicitly raised HTTPException, e.g. the service-level checks) or FastAPI's structured
    list-of-errors (a pydantic model_validator ValueError on request-body validation, e.g. EventTypeCreate)."""
    detail = resp.json().get("detail")
    if isinstance(detail, str):
        return text in detail
    if isinstance(detail, list):
        return any(text in str(item.get("msg", item)) for item in detail)
    return text in str(detail)


def event(**attrs):
    """A minimal event-like object, same convention as tests/test_event_modules_config.py."""
    base = dict(
        event_type=None, modules=None, pricing_type="free", price=None, ticket_types=[], sessions=[],
        delivery_mode="in_person", meeting_link=None, meeting_provider=None, custom_fields=[],
        custom_values=[], form_configuration_version_id=None,
    )
    base.update(attrs)
    return SimpleNamespace(**base)


# ===========================================================================
# pure unit coverage
# ===========================================================================


class TestValidateModuleOverridesRejectsRegistrationFalse:
    def test_registration_false_is_rejected_unconditionally(self):
        """No event_type is involved here at all — the gap this closes: check_type_constraints only
        fires when an Event Type record exists, so a plain override needs its own, unconditional rule."""
        with pytest.raises(EventModuleConfigError, match="registration"):
            validate_module_overrides({"registration": False}, delivery_mode="in_person", is_paid=False)

    def test_registration_true_is_always_fine(self):
        validate_module_overrides({"registration": True}, delivery_mode="in_person", is_paid=False)

    def test_registration_absent_is_unaffected(self):
        validate_module_overrides({"meals": False}, delivery_mode="in_person", is_paid=False)

    def test_other_generic_rules_are_unaffected_by_the_new_check(self):
        """H: other modules remain configurable exactly as before."""
        validate_module_overrides({"tickets": False}, delivery_mode="in_person", is_paid=False)
        with pytest.raises(EventModuleConfigError, match="tickets"):
            validate_module_overrides({"tickets": False}, delivery_mode="in_person", is_paid=True)
        with pytest.raises(EventModuleConfigError, match="online_meeting"):
            validate_module_overrides({"online_meeting": True}, delivery_mode="in_person", is_paid=False)


class TestLegacyAndReadSideResolutionKeepsRegistrationOn:
    def test_f_a_legacy_event_with_no_stored_modules_resolves_registration_true(self):
        assert legacy_modules(event())["registration"] is True
        assert resolve_event_modules(event(modules=None))["registration"] is True

    def test_f_a_stored_modules_dict_that_somehow_disabled_registration_still_resolves_true(self):
        """Defense in depth: every legitimate write path now refuses to persist registration=False, but
        the read side never trusts that alone — a stray/legacy row can never resolve as disabled."""
        poisoned = flags("check_in")  # registration explicitly False here
        assert resolve_event_modules(event(modules=poisoned))["registration"] is True

    def test_other_stored_values_are_unaffected_by_the_registration_backstop(self):
        """H: the backstop only ever touches the registration key."""
        stored = flags("check_in", "meals")
        resolved = resolve_event_modules(event(modules=stored))
        assert resolved["meals"] is True and resolved["tickets"] is False and resolved["registration"] is True


# ===========================================================================
# HTTP-level: Event create/update (A, B, C, G, H, I)
# ===========================================================================


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
    return client_for(env.db, env.owner)


def body(**extra):
    start = datetime.utcnow() + timedelta(days=5)
    return {"title": "Registration Mandatory Summit", "category": "Wellness", "start_date": start.isoformat(),
            "end_date": (start + timedelta(hours=4)).isoformat(), "status": "draft", **extra}


def create_event(env, **extra):
    return staff_client(env).post(f"{API}/", json=body(**extra))


class TestEventCreate:
    def test_a_registration_true_succeeds(self, env):
        resp = create_event(env, modules={"registration": True})
        assert resp.status_code == 201, resp.text
        assert resp.json()["modules"]["registration"] is True

    def test_a_no_modules_sent_at_all_still_defaults_registration_on(self, env):
        resp = create_event(env)
        assert resp.status_code == 201, resp.text
        assert resp.json()["modules"]["registration"] is True

    def test_b_registration_false_is_rejected(self, env):
        resp = create_event(env, modules={"registration": False})
        assert resp.status_code == 422
        assert "registration" in resp.json()["detail"]

    def test_b_registration_false_is_rejected_even_with_other_valid_overrides(self, env):
        resp = create_event(env, modules={"registration": False, "meals": False})
        assert resp.status_code == 422
        assert "registration" in resp.json()["detail"]

    def test_b_nothing_is_created_when_registration_false_is_rejected(self, env):
        from event_sql_support import Event

        create_event(env, modules={"registration": False})
        assert env.db.query(Event).count() == 0

    def test_i_paid_tickets_validation_is_unaffected(self, env):
        """The pre-existing rule (tickets cannot be off for a paid event) still fires independently."""
        resp = create_event(env, pricing_type="paid", price="10", modules={"tickets": False})
        assert resp.status_code == 422 and "tickets" in resp.json()["detail"]

    def test_i_a_paid_event_still_gets_ticketing_enabled_by_default(self, env):
        resp = create_event(env, pricing_type="paid", price="10")
        assert resp.status_code == 201, resp.text
        assert resp.json()["modules"]["tickets"] is True and resp.json()["modules"]["registration"] is True


class TestEventUpdate:
    def test_c_registration_false_is_rejected(self, env):
        event_id = create_event(env).json()["id"]
        resp = staff_client(env).put(f"{API}/{event_id}", json={"modules": {"registration": False}})
        assert resp.status_code == 422
        assert "registration" in resp.json()["detail"]

    def test_c_the_event_is_left_completely_untouched(self, env):
        event_id = create_event(env, modules={"meals": False}).json()["id"]
        before = staff_client(env).get(f"{API}/{event_id}").json()
        resp = staff_client(env).put(f"{API}/{event_id}", json={"modules": {"registration": False}})
        assert resp.status_code == 422
        after = staff_client(env).get(f"{API}/{event_id}").json()
        assert after["modules"] == before["modules"] and after["title"] == before["title"]

    def test_c_registration_false_is_rejected_alongside_an_unrelated_field_change(self, env):
        event_id = create_event(env).json()["id"]
        resp = staff_client(env).put(f"{API}/{event_id}", json={"title": "New Title", "modules": {"registration": False}})
        assert resp.status_code == 422
        assert staff_client(env).get(f"{API}/{event_id}").json()["title"] != "New Title"

    def test_g_an_unrelated_update_keeps_registration_enabled(self, env):
        event_id = create_event(env).json()["id"]
        resp = staff_client(env).put(f"{API}/{event_id}", json={"title": "Renamed"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["title"] == "Renamed" and resp.json()["modules"]["registration"] is True

    def test_h_other_modules_remain_freely_configurable(self, env):
        """H: only registration became mandatory; every other module still toggles normally."""
        event_id = create_event(env, modules={"meals": True}).json()["id"]
        resp = staff_client(env).put(f"{API}/{event_id}", json={"modules": {"meals": False, "check_in": False}})
        assert resp.status_code == 200, resp.text
        assert resp.json()["modules"]["meals"] is False and resp.json()["modules"]["check_in"] is False
        assert resp.json()["modules"]["registration"] is True

    def test_i_tickets_cannot_be_disabled_for_a_paid_event_on_update_either(self, env):
        event_id = create_event(env, pricing_type="paid", price="10").json()["id"]
        resp = staff_client(env).put(f"{API}/{event_id}", json={"modules": {"tickets": False}})
        assert resp.status_code == 422 and "tickets" in resp.json()["detail"]


# ===========================================================================
# HTTP-level: Event Type configuration (D, E)
# ===========================================================================


class TestEventTypeAlwaysRequiresRegistration:
    def test_d_every_seeded_legacy_event_type_resolves_registration_as_required(self, env):
        """The 7 Phase 2.2 types were seeded (070237a7c1cf) with required_modules fully empty, before
        this rule existed. They must still resolve as requiring registration, with no migration."""
        types = client_for(env.db, None).get(f"{TYPES_API}/").json()
        assert len(types) == 7
        for event_type in types:
            assert event_type["required_modules"]["registration"] is True, event_type["key"]
            assert event_type["default_modules"]["registration"] is True
            assert event_type["allowed_modules"]["registration"] is True

    def test_d_a_freshly_created_event_type_with_no_required_modules_still_requires_registration(self, env):
        from event_sql_support import super_admin_user

        resp = client_for(env.db, super_admin_user()).post(f"{TYPES_API}/", json={
            "key": "quiet_gathering", "name": "Quiet Gathering", "default_modules": flags("registration", "check_in"),
        })
        assert resp.status_code == 201, resp.text
        assert resp.json()["required_modules"] == flags("registration")

    def test_e_creating_an_event_type_cannot_disable_registration_in_required_modules(self, env):
        from event_sql_support import super_admin_user

        resp = client_for(env.db, super_admin_user()).post(f"{TYPES_API}/", json={
            "key": "loophole", "name": "Loophole", "default_modules": flags("registration"),
            "required_modules": flags(),  # explicitly tries to require nothing
        })
        assert resp.status_code == 422 and detail_mentions(resp, "registration")

    def test_e_creating_an_event_type_cannot_disable_registration_in_default_or_allowed_modules(self, env):
        from event_sql_support import super_admin_user

        no_default = client_for(env.db, super_admin_user()).post(f"{TYPES_API}/", json={
            "key": "no_default_reg", "name": "No Default Reg", "default_modules": flags("check_in"),
        })
        assert no_default.status_code == 422 and detail_mentions(no_default, "registration")

        no_allowed = client_for(env.db, super_admin_user()).post(f"{TYPES_API}/", json={
            "key": "no_allowed_reg", "name": "No Allowed Reg", "default_modules": flags("registration"),
            "allowed_modules": flags("check_in"),
        })
        assert no_allowed.status_code == 422 and detail_mentions(no_allowed, "registration")

    def test_e_updating_an_event_type_cannot_disable_registration(self, env):
        from event_sql_support import super_admin_user

        admin = client_for(env.db, super_admin_user())
        created = admin.post(f"{TYPES_API}/", json={
            "key": "editable_type", "name": "Editable Type", "default_modules": flags("registration", "check_in"),
        }).json()
        resp = admin.patch(f"{TYPES_API}/{created['id']}", json={"required_modules": flags()})
        assert resp.status_code == 422 and "registration" in resp.json()["detail"]
        resp = admin.patch(f"{TYPES_API}/{created['id']}", json={"default_modules": flags("check_in")})
        assert resp.status_code == 422 and "registration" in resp.json()["detail"]

    def test_e_an_update_that_touches_config_heals_a_legacy_required_modules_row(self, env):
        """A seeded row's required_modules.registration is False at rest (seeded before this rule
        existed). Any update that touches configuration heals it to True, without erroring."""
        from event_sql_support import EventTypeConfig, super_admin_user

        row = env.db.query(EventTypeConfig).filter(EventTypeConfig.key == "conference").first()
        assert row.required_modules["registration"] is False  # the actual seeded value, at rest

        resp = client_for(env.db, super_admin_user()).patch(
            f"{TYPES_API}/{row.id}", json={"allowed_modules": flags(*SPEC_KEYS)})
        assert resp.status_code == 200, resp.text
        assert resp.json()["required_modules"]["registration"] is True

        env.db.expire_all()
        healed = env.db.query(EventTypeConfig).filter(EventTypeConfig.key == "conference").first()
        assert healed.required_modules["registration"] is True  # persisted, not just displayed

    def test_e_other_module_maps_are_still_validated_exactly_as_before(self, env):
        """The generic default/allowed/required consistency check (unrelated to registration) is unchanged."""
        from event_sql_support import super_admin_user

        resp = client_for(env.db, super_admin_user()).post(f"{TYPES_API}/", json={
            "key": "inconsistent", "name": "Inconsistent",
            "default_modules": flags("registration", "meals"), "allowed_modules": flags("registration"),
        })
        assert resp.status_code == 422 and detail_mentions(resp, "meals")
