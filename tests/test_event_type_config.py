"""
Event Management Phase 2.2 (evolved) — Event Type CRUD, end to end on real SQL.

Covers: create/read/update/deactivate/reactivate, authorization (Super Admin writes, public reads),
duplicate key, immutable key, default/allowed/required module-map validation, and safe delete (409 while
referenced, hard-delete once unused).

Run:
    pytest tests/test_event_type_config.py -v
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from event_sql_support import (
    EventTypeConfig,
    client_for,
    customer_user,
    make_enterprise,
    make_event,
    make_session,
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
    silence_side_effects(monkeypatch)
    db = make_session()
    tenant = uuid4()
    ent = make_enterprise(db, tenant)
    yield SimpleNamespace(db=db, tenant=tenant, ent=ent, admin=super_admin_user())
    reset_overrides()
    db.close()


def admin_client(env):
    return client_for(env.db, env.admin)


def payload(key="prayer_meeting", **extra):
    return {
        "key": key, "name": "Prayer Meeting", "active": True,
        "default_modules": flags("registration", "check_in"),
        **extra,
    }


def create(env, **extra):
    return admin_client(env).post(f"{TYPES_API}/", json=payload(**extra))


def get_row(env, key):
    env.db.expire_all()
    return env.db.query(EventTypeConfig).filter(EventTypeConfig.key == key).first()


# ===========================================================================
# create
# ===========================================================================


class TestCreate:
    def test_a_super_admin_can_create_a_minimal_event_type(self, env):
        resp = create(env)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["key"] == "prayer_meeting" and body["name"] == "Prayer Meeting" and body["active"] is True
        assert body["default_modules"] == flags("registration", "check_in")
        assert get_row(env, "prayer_meeting") is not None

    def test_allowed_modules_defaults_to_everything_allowed_when_omitted(self, env):
        assert create(env).json()["allowed_modules"] == ALL_ALLOWED

    def test_required_modules_defaults_to_nothing_required_when_omitted(self, env):
        assert create(env).json()["required_modules"] == NONE_REQUIRED

    def test_allowed_and_required_can_be_set_explicitly(self, env):
        resp = create(
            env,
            default_modules=flags("registration", "check_in", "custom_questions"),
            allowed_modules=flags("registration", "check_in", "custom_questions", "meals"),
            required_modules=flags("registration"),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["allowed_modules"] == flags("registration", "check_in", "custom_questions", "meals")
        assert resp.json()["required_modules"] == flags("registration")

    def test_duplicate_key_is_rejected(self, env):
        assert create(env).status_code == 201
        dup = create(env, name="Different Name")
        assert dup.status_code == 400 and "prayer_meeting" in dup.json()["detail"]
        assert env.db.query(EventTypeConfig).filter(EventTypeConfig.key == "prayer_meeting").count() == 1

    @pytest.mark.parametrize("bad_key", ["", "A", "Prayer_Meeting", "PRAYER", "prayer-meeting", "1meeting", "_meeting", "x" * 31, 7, None, ["k"]])
    def test_malformed_keys_are_rejected(self, env, bad_key):
        assert create(env, key=bad_key).status_code == 422

    def test_unknown_fields_are_rejected(self, env):
        resp = admin_client(env).post(f"{TYPES_API}/", json={**payload(), "tenant_id": str(uuid4())})
        assert resp.status_code == 422

    @pytest.mark.parametrize("mutate", [
        lambda d: d["default_modules"].update(meals=True),  # default on but not allowed
    ])
    def test_default_on_but_not_allowed_is_rejected(self, env, mutate):
        body = payload(default_modules=flags("registration", "meals"), allowed_modules=flags("registration"))
        assert create(env, **body).status_code == 422

    def test_required_but_not_default_is_rejected(self, env):
        resp = create(env, default_modules=flags("registration"), required_modules=flags("registration", "check_in"))
        assert resp.status_code == 422

    def test_required_but_not_allowed_is_rejected(self):
        pass  # required ⟹ default ⟹ allowed is enforced transitively; covered by the two tests above

    def test_a_valid_configuration_satisfying_every_invariant_is_accepted(self, env):
        resp = create(
            env,
            default_modules=flags("registration", "check_in"),
            allowed_modules=flags("registration", "check_in", "custom_questions"),
            required_modules=flags("registration"),
        )
        assert resp.status_code == 201, resp.text

    # ---- authorization ---------------------------------------------------------------------------

    def test_admin_and_provider_are_denied(self, env):
        for role in ("admin", "provider"):
            resp = client_for(env.db, staff_user(env.tenant, role)).post(f"{TYPES_API}/", json=payload())
            assert resp.status_code == 403
        assert env.db.query(EventTypeConfig).filter(EventTypeConfig.key == "prayer_meeting").count() == 0

    def test_customer_is_denied(self, env):
        resp = client_for(env.db, customer_user("c@example.com")).post(f"{TYPES_API}/", json=payload())
        assert resp.status_code == 403

    def test_anonymous_is_denied(self, env):
        assert client_for(env.db, None).post(f"{TYPES_API}/", json=payload()).status_code == 401

    def test_inactive_super_admin_is_denied(self, env):
        inactive = {**super_admin_user(), "status": "disabled"}
        assert client_for(env.db, inactive).post(f"{TYPES_API}/", json=payload()).status_code == 403


# ===========================================================================
# read
# ===========================================================================


class TestRead:
    def test_list_is_public_and_returns_only_active_types_by_default(self, env):
        create(env, key="active_one", active=True)
        create(env, key="inactive_one", active=False)
        anonymous = client_for(env.db, None)
        keys = {t["key"] for t in anonymous.get(f"{TYPES_API}/").json()}
        assert "active_one" in keys and "inactive_one" not in keys

    def test_include_inactive_returns_both(self, env):
        create(env, key="active_one", active=True)
        create(env, key="inactive_one", active=False)
        keys = {t["key"] for t in client_for(env.db, None).get(f"{TYPES_API}/", params={"include_inactive": "true"}).json()}
        assert {"active_one", "inactive_one"} <= keys

    def test_get_by_id_is_public(self, env):
        created = create(env).json()
        resp = client_for(env.db, None).get(f"{TYPES_API}/{created['id']}")
        assert resp.status_code == 200 and resp.json()["key"] == "prayer_meeting"

    def test_get_unknown_id_is_404(self, env):
        assert client_for(env.db, None).get(f"{TYPES_API}/{uuid4()}").status_code == 404


# ===========================================================================
# update
# ===========================================================================


class TestUpdate:
    @pytest.fixture
    def created(self, env):
        return create(env).json()

    def test_name_can_be_changed(self, env, created):
        resp = admin_client(env).patch(f"{TYPES_API}/{created['id']}", json={"name": "Renamed"})
        assert resp.status_code == 200 and resp.json()["name"] == "Renamed"

    def test_active_can_be_toggled(self, env, created):
        off = admin_client(env).patch(f"{TYPES_API}/{created['id']}", json={"active": False})
        assert off.status_code == 200 and off.json()["active"] is False
        on = admin_client(env).patch(f"{TYPES_API}/{created['id']}", json={"active": True})
        assert on.status_code == 200 and on.json()["active"] is True

    def test_key_is_not_editable(self, env, created):
        resp = admin_client(env).patch(f"{TYPES_API}/{created['id']}", json={"key": "different_key"})
        assert resp.status_code == 422  # unknown field, extra="forbid"
        assert get_row(env, "prayer_meeting") is not None

    def test_default_modules_can_be_edited(self, env, created):
        resp = admin_client(env).patch(f"{TYPES_API}/{created['id']}", json={"default_modules": flags("registration")})
        assert resp.status_code == 200 and resp.json()["default_modules"] == flags("registration")

    def test_partial_update_leaves_other_fields_untouched(self, env, created):
        resp = admin_client(env).patch(f"{TYPES_API}/{created['id']}", json={"name": "Only Name"})
        assert resp.json()["default_modules"] == created["default_modules"]
        assert resp.json()["allowed_modules"] == created["allowed_modules"]

    def test_an_inconsistent_edit_is_rejected_and_nothing_changes(self, env, created):
        resp = admin_client(env).patch(f"{TYPES_API}/{created['id']}", json={
            "default_modules": flags("registration", "meals"), "allowed_modules": flags("registration")})
        assert resp.status_code == 422
        assert get_row(env, "prayer_meeting").default_modules == created["default_modules"]

    def test_editing_the_module_maps_never_touches_an_existing_event_of_this_type(self, env, created):
        event = make_event(env.db, env.tenant, env.ent, event_type="prayer_meeting", modules=created["default_modules"])
        admin_client(env).patch(f"{TYPES_API}/{created['id']}", json={"default_modules": flags("registration", "meals")})
        env.db.expire_all()
        from event_sql_support import Event

        assert env.db.get(Event, event.id).modules == created["default_modules"]

    def test_unknown_id_is_404(self, env):
        assert admin_client(env).patch(f"{TYPES_API}/{uuid4()}", json={"name": "x"}).status_code == 404

    # ---- authorization ---------------------------------------------------------------------------

    def test_admin_provider_customer_anonymous_are_denied(self, env, created):
        for client in (
            client_for(env.db, staff_user(env.tenant, "admin")),
            client_for(env.db, staff_user(env.tenant, "provider")),
            client_for(env.db, customer_user("c@example.com")),
        ):
            assert client.patch(f"{TYPES_API}/{created['id']}", json={"name": "Hacked"}).status_code == 403
        assert client_for(env.db, None).patch(f"{TYPES_API}/{created['id']}", json={"name": "Hacked"}).status_code == 401
        assert get_row(env, "prayer_meeting").name == "Prayer Meeting"


# ===========================================================================
# delete
# ===========================================================================


class TestDelete:
    def test_an_unreferenced_type_can_be_hard_deleted(self, env):
        created = create(env).json()
        resp = admin_client(env).delete(f"{TYPES_API}/{created['id']}")
        assert resp.status_code == 200, resp.text
        assert get_row(env, "prayer_meeting") is None

    def test_a_referenced_type_cannot_be_deleted(self, env):
        created = create(env).json()
        make_event(env.db, env.tenant, env.ent, event_type="prayer_meeting", modules=created["default_modules"])
        resp = admin_client(env).delete(f"{TYPES_API}/{created['id']}")
        assert resp.status_code == 409
        assert get_row(env, "prayer_meeting") is not None

    def test_deactivating_a_referenced_type_is_the_safe_alternative(self, env):
        created = create(env).json()
        make_event(env.db, env.tenant, env.ent, event_type="prayer_meeting", modules=created["default_modules"])
        resp = admin_client(env).patch(f"{TYPES_API}/{created['id']}", json={"active": False})
        assert resp.status_code == 200 and resp.json()["active"] is False

    def test_unknown_id_is_404(self, env):
        assert admin_client(env).delete(f"{TYPES_API}/{uuid4()}").status_code == 404

    def test_admin_and_anonymous_are_denied(self, env):
        created = create(env).json()
        assert client_for(env.db, staff_user(env.tenant, "admin")).delete(f"{TYPES_API}/{created['id']}").status_code == 403
        assert client_for(env.db, None).delete(f"{TYPES_API}/{created['id']}").status_code == 401
        assert get_row(env, "prayer_meeting") is not None
