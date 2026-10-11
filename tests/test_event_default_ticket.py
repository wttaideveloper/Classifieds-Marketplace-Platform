"""Backend-generated default ticket id for Base-Price-only Events.

(Base Price next to configured Regular/VIP tickets is covered in test_event_default_ticket_coexistence.py.)

Runs the real quote / checkout / walk-in / create / update code on in-memory SQLite (no network).
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.models.event_aux_models import EventOrder, EventRegistration
from app.models.event_form_config_model import EventFormConfiguration, EventFormConfigurationVersion
from app.schemas.event_schema import EventCreate, EventUpdate
from app.services import event_service
from app.services.event_form_config_service import core_field_enabled
from app.utils.event_default_ticket import DEFAULT_TICKET_NAME, base_price_is_purchasable
from event_sql_support import (
    API,
    Event,
    client_for,
    customer_user,
    make_enterprise,
    make_event,
    make_session,
    reset_overrides,
    silence_side_effects,
    staff_user,
)


@pytest.fixture
def env(monkeypatch):
    silence_side_effects(monkeypatch)
    db = make_session()
    tenant = uuid4()
    ent = make_enterprise(db, tenant)
    state = {"version_id": None}
    monkeypatch.setattr(
        "app.services.event_form_config_service.apply_form_configuration_to_event_data",
        lambda _db, data, user: {"tenant_id": tenant, "enterprise_id": ent.id, "form_configuration_id": None,
                                 "form_configuration_version_id": state["version_id"], "custom_values": []},
    )
    yield SimpleNamespace(db=db, tenant=tenant, ent=ent, owner=staff_user(tenant, "admin"), state=state)
    reset_overrides()
    db.close()


def create(env, **overrides):
    values = dict(
        tenant_id=env.tenant, enterprise_id=env.ent.id, title="Base price event", description="d", category="Wellness",
        organiser_name="Org", organiser_contact="o@example.com", start_date=datetime.utcnow() + timedelta(days=7),
        end_date=datetime.utcnow() + timedelta(days=8), pricing_type="paid", price="500", currency="INR", capacity="2",
        status="draft",
    )
    values.update(overrides)
    return event_service.create_event_service(env.db, EventCreate(**values), env.owner)


def stored(env, event_id):
    env.db.expire_all()
    return env.db.get(Event, event_id if isinstance(event_id, UUID) else UUID(str(event_id)))


def published(env, **overrides):
    """A published Base-Price-only Event created through the service (so it really gets an id)."""
    response = create(env, **overrides)
    row = stored(env, response.id)
    row.status = "published"
    env.db.commit()
    return row


def quote(env, event, ticket_id, user=None, quantity=1):
    return client_for(env.db, user or customer_user("q@example.com")).post(
        f"{API}/{event.id}/checkout/quote", json={"ticket_type_id": ticket_id, "quantity": quantity})


def checkout(env, event, email, ticket_id, quantity=1):
    return client_for(env.db, customer_user(email)).post(
        f"{API}/{event.id}/checkout",
        json={"participant_name": "A", "participant_email": email, "ticket_type_id": ticket_id, "quantity": quantity})


# --- eligibility / assignment -----------------------------------------------------------------

def test_eligible_event_gets_a_stable_backend_generated_id_exposed_in_the_response(env):
    response = create(env)
    assert response.default_ticket_type_id is not None
    UUID(response.default_ticket_type_id)  # a real UUID, generated server-side
    assert stored(env, response.id).default_ticket_type_id == UUID(response.default_ticket_type_id)
    assert response.ticket_types in (None, [])  # nothing synthetic is inserted into ticket_types


def test_repeated_reads_and_unrelated_updates_keep_the_id(env):
    created = create(env)
    first = client_for(env.db, env.owner).get(f"{API}/{created.id}").json()["default_ticket_type_id"]
    second = client_for(env.db, env.owner).get(f"{API}/{created.id}").json()["default_ticket_type_id"]
    assert first == second == created.default_ticket_type_id

    updated = event_service.update_event_service(env.db, created.id, EventUpdate(title="Renamed"), env.owner)
    assert updated.default_ticket_type_id == created.default_ticket_type_id
    again = event_service.update_event_service(env.db, created.id, EventUpdate(price="750"), env.owner)
    assert again.default_ticket_type_id == created.default_ticket_type_id and again.price == "750"


@pytest.mark.parametrize(
    "overrides",
    [
        {"price": None, "pricing_type": "free"},
        {"price": "0"},
        {"price": "abc"},
        {"price": "-5"},
    ],
)
def test_ineligible_events_get_no_id(env, overrides):
    assert create(env, **overrides).default_ticket_type_id is None


def test_invalid_currency_is_not_eligible():
    assert base_price_is_purchasable("500", "INR", []) is True
    assert base_price_is_purchasable("500", "RUPEES", []) is False
    assert base_price_is_purchasable("500", None, []) is False


def test_a_draft_gets_its_id_when_an_update_makes_it_eligible_and_never_a_replacement(env):
    created = create(env, price=None, pricing_type="free")
    assert created.default_ticket_type_id is None
    priced = event_service.update_event_service(env.db, created.id, EventUpdate(price="300", pricing_type="paid"), env.owner)
    assigned = priced.default_ticket_type_id
    assert assigned is not None
    # adding a VIP ticket does NOT disturb it: Base Price and configured tickets coexist
    with_tickets = event_service.update_event_service(
        env.db, created.id, EventUpdate(ticket_types=[{"name": "VIP", "price": "900"}]), env.owner)
    assert with_tickets.default_ticket_type_id == assigned
    # becomes ineligible (price removed): id hidden, stored value kept
    unpriced = event_service.update_event_service(env.db, created.id, EventUpdate(price="0"), env.owner)
    assert unpriced.default_ticket_type_id is None
    assert str(stored(env, created.id).default_ticket_type_id) == assigned
    # eligible again: the SAME id returns, no replacement is generated
    repriced = event_service.update_event_service(env.db, created.id, EventUpdate(price="300"), env.owner)
    assert repriced.default_ticket_type_id == assigned


def test_existing_events_are_not_backfilled_but_can_be_enabled_explicitly(env):
    legacy = make_event(env.db, env.tenant, env.ent, pricing_type="paid", price="500", currency="INR", ticket_types=[])
    assert legacy.default_ticket_type_id is None

    # an unrelated edit does not enable it, nor does re-sending its price unchanged
    event_service.update_event_service(env.db, legacy.id, EventUpdate(title="Edited"), env.owner)
    event_service.update_event_service(env.db, legacy.id, EventUpdate(price="500"), env.owner)
    assert stored(env, legacy.id).default_ticket_type_id is None

    # quote with an invented id is refused
    assert quote(env, stored(env, legacy.id), str(uuid4())).status_code == 404

    assert event_service.enable_default_ticket_type(env.db, stored(env, legacy.id)) is True
    first = stored(env, legacy.id).default_ticket_type_id
    assert first is not None
    assert event_service.enable_default_ticket_type(env.db, stored(env, legacy.id)) is False  # never replaced
    assert stored(env, legacy.id).default_ticket_type_id == first


def test_base_price_disabled_in_the_form_gives_no_default_ticket(env):
    config = EventFormConfiguration(id=uuid4(), name="No price", scope="global", status="published", is_active=True)
    env.db.add(config)
    version = EventFormConfigurationVersion(
        id=uuid4(), configuration_id=config.id, version=1, status="published",
        sections=[{"id": "s", "stable_key": "s", "title": "S", "is_enabled": True, "fields": [
            {"id": "f1", "source": "core", "core_key": "title", "is_enabled": True},
            {"id": "f2", "source": "core", "core_key": "price", "is_enabled": False},
        ]}],
    )
    env.db.add(version)
    env.db.commit()
    with_price = EventFormConfigurationVersion(
        id=uuid4(), configuration_id=config.id, version=2, status="published",
        sections=[{"id": "s", "stable_key": "s", "title": "S", "is_enabled": True, "fields": [
            {"id": "f2", "source": "core", "core_key": "price", "is_enabled": True}]}],
    )
    env.db.add(with_price)
    env.db.commit()
    assert core_field_enabled(env.db, version.id, "price") is False
    assert core_field_enabled(env.db, with_price.id, "price") is True
    assert core_field_enabled(env.db, uuid4(), "price") is False   # unknown version fails closed
    assert core_field_enabled(env.db, None, "price") is True       # legacy Event without a version

    env.state["version_id"] = version.id
    assert create(env).default_ticket_type_id is None
    env.state["version_id"] = with_price.id
    assert create(env).default_ticket_type_id is not None


def test_duplicate_gets_its_own_id(env):
    created = create(env)
    clone = event_service.duplicate_event_service(env.db, created.id, env.owner)
    assert clone.default_ticket_type_id is not None and clone.default_ticket_type_id != created.default_ticket_type_id


# --- quote / checkout / walk-in ---------------------------------------------------------------

def test_quote_and_checkout_accept_the_default_ticket_and_use_the_current_price(env):
    event = published(env, capacity="10")
    ticket_id = str(event.default_ticket_type_id)

    q = quote(env, event, ticket_id, quantity=2)
    assert q.status_code == 200, q.text
    assert q.json()["ticket_subtotal"] == 1000.0 and q.json()["currency"] == "INR"

    first = checkout(env, event, "a@example.com", ticket_id)
    assert first.status_code == 201, first.text
    assert first.json()["amount"] == "500.00" and first.json()["ticket_type_id"] == ticket_id

    # price change: new quotes follow it, the historical order keeps its amount and reference
    event_service.update_event_service(env.db, event.id, EventUpdate(price="800"), env.owner)
    assert quote(env, stored(env, event.id), ticket_id).json()["ticket_subtotal"] == 800.0
    env.db.expire_all()
    old = env.db.query(EventOrder).one()
    assert old.amount == "500.00" and old.ticket_type_id == ticket_id
    assert env.db.query(EventRegistration).one().ticket_type_id == ticket_id
    assert stored(env, event.id).default_ticket_type_id == UUID(ticket_id)


def test_event_capacity_is_enforced_through_the_default_ticket(env):
    event = published(env, capacity="2")
    ticket_id = str(event.default_ticket_type_id)
    assert checkout(env, event, "a@example.com", ticket_id).status_code == 201
    assert checkout(env, event, "b@example.com", ticket_id).status_code == 201
    third = checkout(env, event, "c@example.com", ticket_id)
    assert third.status_code == 400 and "capacity" in third.text.lower()
    assert checkout(env, event, "d@example.com", ticket_id, quantity=1).status_code == 400
    assert env.db.query(EventOrder).count() == 2


def test_capacity_check_runs_under_the_event_row_lock(env, monkeypatch):
    event = published(env, capacity="5")
    locked = []
    real_query = env.db.query

    def spy(*entities, **kw):
        q = real_query(*entities, **kw)
        original = q.with_for_update

        def with_for_update(*a, **k):
            locked.append(True)
            return original(*a, **k)

        q.with_for_update = with_for_update
        return q

    monkeypatch.setattr(env.db, "query", spy)
    assert checkout(env, event, "a@example.com", str(event.default_ticket_type_id)).status_code == 201
    assert locked


@pytest.mark.parametrize("bad", ["not-a-ticket", "00000000-0000-0000-0000-000000000000"])
def test_invalid_ids_still_404(env, bad):
    event = published(env)
    assert quote(env, event, bad).status_code == 404
    assert checkout(env, event, "a@example.com", bad).status_code == 404
    assert env.db.query(EventOrder).count() == 0


def test_the_default_id_stops_working_when_the_event_is_no_longer_eligible(env):
    event = published(env)
    ticket_id = str(event.default_ticket_type_id)
    event_service.update_event_service(env.db, event.id, EventUpdate(price="0"), env.owner)
    assert quote(env, stored(env, event.id), ticket_id).status_code == 404
    assert checkout(env, stored(env, event.id), "a@example.com", ticket_id).status_code == 404


def test_configured_tickets_are_unchanged(env):
    event = make_event(
        env.db, env.tenant, env.ent, pricing_type="paid", price="500", currency="INR", capacity="5",
        ticket_types=[{"id": "regular", "name": "Regular", "price": "500"}, {"id": "vip", "name": "VIP", "price": "900", "capacity": 1}],
        default_ticket_type_id=uuid4(),  # a stored id is sellable next to real tickets (they are independent options)
    )
    assert quote(env, event, "regular").json()["ticket_subtotal"] == 500.0
    assert quote(env, event, "vip").json()["ticket_subtotal"] == 900.0
    assert checkout(env, event, "a@example.com", "vip").status_code == 201
    assert checkout(env, event, "b@example.com", "vip").status_code == 400   # per-ticket capacity untouched
    # the default ticket sells at the Base Price, independently of Regular/VIP (which keep their own ids and prices)
    assert quote(env, event, str(event.default_ticket_type_id)).json()["ticket_subtotal"] == 500.0
    assert quote(env, event, str(uuid4())).status_code == 404
    shown = client_for(env.db, env.owner).get(f"{API}/{event.id}").json()
    assert shown["default_ticket_type_id"] == str(event.default_ticket_type_id)
    assert [t["id"] for t in shown["ticket_types"]] == ["regular", "vip"]   # never written into ticket_types


def test_mixed_currency_safeguards_still_apply(env):
    event = published(env, currency="USD", capacity="5")
    ticket_id = str(event.default_ticket_type_id)
    assert quote(env, event, ticket_id).json()["currency"] == "USD"
    updated = stored(env, event.id)
    updated.meals = {"enabled": True, "options": [{"id": "lunch", "name": "Lunch", "price": "10", "currency": "INR"}]}
    updated.modules = {**(updated.modules or {}), "meals": True, "tickets": True}
    env.db.commit()
    mixed = client_for(env.db, customer_user("m@example.com")).post(
        f"{API}/{event.id}/checkout/quote", json={"ticket_type_id": ticket_id, "meal_selections": ["lunch"]})
    assert mixed.status_code >= 400


def test_walk_in_uses_the_default_ticket_with_the_existing_payment_rules(env):
    event = published(env, capacity="5")
    ticket_id = str(event.default_ticket_type_id)
    resp = client_for(env.db, env.owner).post(
        f"{API}/{event.id}/walk-in",
        json={"participant_name": "Walk In", "participant_email": "w@example.com", "ticket_type_id": ticket_id})
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    assert body["payment"]["required"] is True          # priced: a pending order, not a recorded payment
    assert body["ticket"]["ticket_type_id"] == ticket_id
    env.db.expire_all()
    registration = env.db.query(EventRegistration).one()
    assert registration.ticket_type_id == ticket_id and registration.status != "attended"  # not checked in unpaid
    bad = client_for(env.db, env.owner).post(
        f"{API}/{event.id}/walk-in",
        json={"participant_name": "X", "participant_email": "x@example.com", "ticket_type_id": str(uuid4())})
    assert bad.status_code in (400, 404)


# --- migration ---------------------------------------------------------------------------------

def test_migration_is_additive_nullable_and_chains_onto_the_event_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script_dir = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = script_dir.get_revision("e1f4a7c9b3d5")
    assert revision.down_revision == "c4d8e9f1a2b3"
    assert "e1f4a7c9b3d5" in script_dir.get_heads()
    import inspect
    import importlib.util
    spec = importlib.util.spec_from_file_location("m", revision.path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = inspect.getsource(module)
    assert "add_column" in source and "nullable=True" in source
    assert "op.execute" not in source and "bulk_insert" not in source   # no backfill
    assert Event.__table__.c.default_ticket_type_id.nullable is True


def test_all_purchase_flows_recheck_that_base_price_is_enabled_in_the_form(env):
    config = EventFormConfiguration(id=uuid4(), name="Priced", scope="global", status="published", is_active=True)
    env.db.add(config)
    version = EventFormConfigurationVersion(
        id=uuid4(), configuration_id=config.id, version=1, status="published",
        sections=[{"id": "s", "stable_key": "s", "title": "S", "is_enabled": True, "fields": [
            {"id": "f", "source": "core", "core_key": "price", "is_enabled": True}]}],
    )
    env.db.add(version)
    env.db.commit()
    env.state["version_id"] = version.id
    event = published(env, capacity="5")
    ticket_id = str(event.default_ticket_type_id)
    assert quote(env, event, ticket_id).status_code == 200

    # Base Price no longer enabled in the Event's form: the stored id is kept but nothing sells it
    version.sections = [{"id": "s", "stable_key": "s", "title": "S", "is_enabled": True, "fields": [
        {"id": "f", "source": "core", "core_key": "price", "is_enabled": False}]}]
    env.db.commit()
    event = stored(env, event.id)
    assert quote(env, event, ticket_id).status_code == 404
    assert checkout(env, event, "a@example.com", ticket_id).status_code == 404
    walk = client_for(env.db, env.owner).post(
        f"{API}/{event.id}/walk-in",
        json={"participant_name": "W", "participant_email": "w@example.com", "ticket_type_id": ticket_id})
    assert walk.status_code in (400, 404)
    assert env.db.query(EventOrder).count() == 0 and env.db.query(EventRegistration).count() == 0
    assert stored(env, event.id).default_ticket_type_id == UUID(ticket_id)
