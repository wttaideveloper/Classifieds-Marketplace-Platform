"""
Base Price (default ticket) and configured Ticket Types are independent, purchasable options.

Production report (Event da6fef76-...): paid, price 100 INR, a configured VIP ticket at 150, published,
modules.tickets/registration true -- yet ``default_ticket_type_id`` was null, so only VIP could be bought.

Root cause: ``base_price_is_purchasable`` required NO configured Ticket Types, so (1) creating the Event never
assigned an id, (2) the response mapper hid any id (purchasable_default_ticket_id) and (3) the shared resolver
(``_resolve_ticket``) refused a default id next to real tickets. Any Event with a VIP/Regular ticket was therefore
permanently ineligible. Eligibility is now: positive Base Price + valid Event currency + Base Price enabled in the
pinned Event Form -- configured tickets no longer matter.

Run:
    pytest tests/test_event_default_ticket_coexistence.py -v
"""
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.models.event_aux_models import EventOrder, EventRegistration
from app.models.event_form_config_model import EventFormConfiguration, EventFormConfigurationVersion
from app.schemas.event_schema import EventUpdate
from app.services import event_service
from app.utils.event_default_ticket import base_price_is_purchasable, resolve_ticket_option
from event_sql_support import API, Event, client_for, customer_user, make_event, staff_user

# fixtures + helpers shared with the base-price-only suite
from test_event_default_ticket import checkout, create, env, published, quote, stored  # noqa: F401

VIP = {"id": "vip", "name": "VIP", "price": "150"}
REGULAR = {"id": "regular", "name": "Regular", "price": "120"}


def vip_event(env, tickets=None, **overrides):
    return published(env, price="100", currency="INR", capacity="10", ticket_types=tickets if tickets is not None else [dict(VIP)], **overrides)


def disabled_price_version(env, enabled: bool):
    config = EventFormConfiguration(id=uuid4(), name=f"cfg-{uuid4()}", scope="global", status="published", is_active=True)
    env.db.add(config)
    version = EventFormConfigurationVersion(
        id=uuid4(), configuration_id=config.id, version=1, status="published",
        sections=[{"id": "s", "stable_key": "s", "title": "S", "is_enabled": True, "fields": [
            {"id": "f", "source": "core", "core_key": "price", "is_enabled": enabled}]}],
    )
    env.db.add(version)
    env.db.commit()
    return version


class TestEligibility:
    def test_configured_tickets_no_longer_make_base_price_ineligible(self):
        assert base_price_is_purchasable("100", "INR", [VIP]) is True
        assert base_price_is_purchasable("100", "INR", [VIP, REGULAR]) is True

    @pytest.mark.parametrize("price, currency", [(None, "INR"), ("0", "INR"), ("-1", "INR"), ("abc", "INR"), ("100", "RUPEES"), ("100", None)])
    def test_still_ineligible_without_a_positive_price_or_valid_currency(self, price, currency):
        assert base_price_is_purchasable(price, currency, [VIP]) is False


class TestCreation:
    def test_base_price_only_event(self, env):
        r = create(env, price="100", currency="INR")
        assert r.default_ticket_type_id is not None and not r.ticket_types

    def test_base_price_plus_vip_gets_an_id_and_keeps_vip_untouched(self, env):
        r = create(env, price="100", currency="INR", ticket_types=[dict(VIP)])
        assert r.default_ticket_type_id is not None
        UUID(r.default_ticket_type_id)
        assert [(t["id"], t["name"], t["price"]) for t in r.ticket_types] == [("vip", "VIP", "150")]   # nothing synthetic added
        assert [t["id"] for t in stored(env, r.id).ticket_types] == ["vip"]

    def test_base_price_plus_regular_and_vip(self, env):
        r = create(env, price="100", currency="INR", ticket_types=[dict(REGULAR), dict(VIP)])
        assert r.default_ticket_type_id is not None
        assert [t["id"] for t in r.ticket_types] == ["regular", "vip"]

    def test_tickets_only_event_without_a_base_price_gets_no_default(self, env):
        r = create(env, price=None, ticket_types=[dict(VIP)])
        assert r.default_ticket_type_id is None

    def test_free_event_never_gets_one(self, env):
        assert create(env, pricing_type="free", price=None, ticket_types=[]).default_ticket_type_id is None

    def test_a_ticket_with_a_mismatched_currency_is_still_rejected(self, env):
        with pytest.raises(HTTPException) as exc:
            create(env, price="100", currency="INR", ticket_types=[{"id": "vip", "name": "VIP", "price": "150", "currency": "USD"}])
        assert exc.value.status_code == 422

    def test_base_price_disabled_in_the_pinned_form_gets_no_default_but_vip_still_sells(self, env):
        env.state["version_id"] = disabled_price_version(env, enabled=False).id
        r = create(env, price="100", currency="INR", ticket_types=[dict(VIP)])
        assert r.default_ticket_type_id is None

    def test_base_price_enabled_in_the_pinned_form_gets_one(self, env):
        env.state["version_id"] = disabled_price_version(env, enabled=True).id
        assert create(env, price="100", currency="INR", ticket_types=[dict(VIP)]).default_ticket_type_id is not None


class TestQuoteAndCheckoutWithBothOptions:
    def test_each_option_is_priced_independently(self, env):
        event = vip_event(env)
        default_id = str(event.default_ticket_type_id)
        d = quote(env, event, default_id, quantity=2)
        v = quote(env, event, "vip", quantity=2)
        assert d.status_code == 200 and v.status_code == 200, (d.text, v.text)
        assert (d.json()["ticket_subtotal"], d.json()["currency"]) == (200.0, "INR")
        assert (v.json()["ticket_subtotal"], v.json()["currency"]) == (300.0, "INR")

    def test_regular_and_vip_and_default_all_sell(self, env):
        event = vip_event(env, tickets=[dict(REGULAR), dict(VIP)])
        default_id = str(event.default_ticket_type_id)
        for email, ticket, amount in (("a@example.com", default_id, "100.00"), ("b@example.com", "regular", "120.00"), ("c@example.com", "vip", "150.00")):
            resp = checkout(env, event, email, ticket)
            assert resp.status_code == 201, resp.text
            assert (resp.json()["amount"], resp.json()["ticket_type_id"]) == (amount, ticket)
        env.db.expire_all()
        assert {r.ticket_type_id for r in env.db.query(EventRegistration).all()} == {default_id, "regular", "vip"}

    def test_the_default_ticket_follows_the_current_base_price(self, env):
        event = vip_event(env)
        default_id = str(event.default_ticket_type_id)
        event_service.update_event_service(env.db, event.id, EventUpdate(price="130"), env.owner)
        assert quote(env, stored(env, event.id), default_id).json()["ticket_subtotal"] == 130.0
        assert quote(env, stored(env, event.id), "vip").json()["ticket_subtotal"] == 150.0

    def test_walk_in_accepts_either_option_and_still_requires_an_id(self, env):
        event = vip_event(env)
        staff = client_for(env.db, env.owner)
        for email, ticket in (("w1@example.com", str(event.default_ticket_type_id)), ("w2@example.com", "vip")):
            resp = staff.post(f"{API}/{event.id}/walk-in", json={"participant_name": "W", "participant_email": email, "ticket_type_id": ticket})
            assert resp.status_code in (200, 201), resp.text
            assert resp.json()["ticket"]["ticket_type_id"] == ticket
        none = staff.post(f"{API}/{event.id}/walk-in", json={"participant_name": "W", "participant_email": "w3@example.com"})
        assert none.status_code == 400 and "ticket_type_id" in none.text


class TestResolverDistinguishesTheCases:
    def test_configured_default_and_invalid(self, env):
        event = vip_event(env)
        default_id = str(event.default_ticket_type_id)
        assert resolve_ticket_option(event, "vip").kind == "configured"
        resolved = resolve_ticket_option(event, default_id)
        assert resolved.kind == "default" and resolved.ticket["price"] == "100" and resolved.ticket["currency"] == "INR"
        assert resolve_ticket_option(event, default_id.upper()).kind == "default"
        for bad in (str(uuid4()), "nope", "VIP"):
            assert resolve_ticket_option(event, bad).kind == "invalid"
        assert resolve_ticket_option(event, None).kind == "flat"

    def test_stored_id_that_is_no_longer_eligible_is_ineligible_not_invalid(self, env):
        event = vip_event(env)
        default_id = str(event.default_ticket_type_id)
        event.price = None
        assert resolve_ticket_option(event, default_id).kind == "ineligible"
        event.price, event.currency = "100", "RUPEES"
        assert resolve_ticket_option(event, default_id).kind == "ineligible"
        event.currency = "INR"
        assert resolve_ticket_option(event, default_id, base_price_enabled=lambda: False).kind == "ineligible"

    def test_a_configured_ticket_wins_a_collision_with_the_default_id(self, env):
        event = vip_event(env)
        event.ticket_types = [{"id": str(event.default_ticket_type_id), "name": "Clash", "price": "999"}]
        assert resolve_ticket_option(event, str(event.default_ticket_type_id)).ticket["price"] == "999"


class TestSafety:
    def test_base_price_disabled_later_blocks_the_default_everywhere_but_not_vip(self, env):
        version = disabled_price_version(env, enabled=True)
        env.state["version_id"] = version.id
        event = vip_event(env)
        default_id = str(event.default_ticket_type_id)
        assert quote(env, event, default_id).status_code == 200
        version.sections = [{"id": "s", "stable_key": "s", "title": "S", "is_enabled": True, "fields": [
            {"id": "f", "source": "core", "core_key": "price", "is_enabled": False}]}]
        env.db.commit()
        event = stored(env, event.id)
        assert quote(env, event, default_id).status_code == 404
        assert checkout(env, event, "a@example.com", default_id).status_code == 404
        walk = client_for(env.db, env.owner).post(f"{API}/{event.id}/walk-in", json={"participant_name": "W", "participant_email": "w@example.com", "ticket_type_id": default_id})
        assert walk.status_code in (400, 404)
        assert quote(env, event, "vip").status_code == 200
        assert checkout(env, event, "b@example.com", "vip").status_code == 201
        assert env.db.query(EventOrder).count() == 1

    def test_unknown_or_stale_ids_are_404_and_create_nothing(self, env):
        event = vip_event(env)
        other = vip_event(env)
        for bad in (str(uuid4()), str(other.default_ticket_type_id), "00000000-0000-0000-0000-000000000000"):
            assert quote(env, event, bad).status_code == 404
            assert checkout(env, event, "a@example.com", bad).status_code == 404
        assert env.db.query(EventOrder).count() == 0

    def test_removing_the_base_price_stops_the_default_but_keeps_vip(self, env):
        event = vip_event(env)
        default_id = str(event.default_ticket_type_id)
        event_service.update_event_service(env.db, event.id, EventUpdate(price="0"), env.owner)
        assert quote(env, stored(env, event.id), default_id).status_code == 404
        assert quote(env, stored(env, event.id), "vip").status_code == 200
        assert client_for(env.db, env.owner).get(f"{API}/{event.id}").json()["default_ticket_type_id"] is None

    def test_default_id_does_not_unlock_anything_the_window_or_status_block(self, env):
        event = vip_event(env)
        default_id = str(event.default_ticket_type_id)
        event.status = "draft"
        env.db.commit()
        assert checkout(env, event, "a@example.com", default_id).status_code == 400
        assert quote(env, event, default_id).status_code == 400


class TestStableId:
    def test_id_survives_every_kind_of_update(self, env):
        created = create(env, price="100", currency="INR", ticket_types=[dict(VIP)])
        assigned = created.default_ticket_type_id
        steps = [
            EventUpdate(title="Renamed"),
            EventUpdate(price="250"),
            EventUpdate(ticket_types=[dict(REGULAR), dict(VIP)]),
            EventUpdate(ticket_types=[]),
            EventUpdate(ticket_types=[dict(VIP)]),
        ]
        for update in steps:
            assert event_service.update_event_service(env.db, created.id, update, env.owner).default_ticket_type_id == assigned
        assert str(stored(env, created.id).default_ticket_type_id) == assigned

    def test_reads_are_stable(self, env):
        created = create(env, price="100", currency="INR", ticket_types=[dict(VIP)])
        a = client_for(env.db, env.owner).get(f"{API}/{created.id}").json()["default_ticket_type_id"]
        b = client_for(env.db, env.owner).get(f"{API}/{created.id}").json()["default_ticket_type_id"]
        assert a == b == created.default_ticket_type_id


class TestCapacity:
    def test_event_capacity_is_shared_by_default_and_configured_tickets(self, env):
        event = vip_event(env, tickets=[{"id": "vip", "name": "VIP", "price": "150"}])
        event.capacity = "2"
        env.db.commit()
        default_id = str(event.default_ticket_type_id)
        assert checkout(env, event, "a@example.com", default_id).status_code == 201
        assert checkout(env, event, "b@example.com", "vip").status_code == 201
        third = checkout(env, event, "c@example.com", default_id)
        assert third.status_code == 400 and "capacity" in third.text.lower()
        assert checkout(env, event, "d@example.com", "vip").status_code == 400
        assert env.db.query(EventOrder).count() == 2

    def test_a_per_ticket_capacity_only_limits_that_ticket(self, env):
        event = vip_event(env, tickets=[{"id": "vip", "name": "VIP", "price": "150", "capacity": 1}])
        default_id = str(event.default_ticket_type_id)
        assert checkout(env, event, "a@example.com", "vip").status_code == 201
        assert checkout(env, event, "b@example.com", "vip").status_code == 400          # VIP is full
        assert checkout(env, event, "c@example.com", default_id).status_code == 201     # the default is not

    def test_capacity_check_still_runs_under_the_event_row_lock(self, env, monkeypatch):
        event = vip_event(env)
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


class TestExistingEventsAndHistory:
    def _legacy_vip_event(self, env):
        # exactly the production shape: paid, price 100 INR, VIP 150, published, NO default id
        return make_event(env.db, env.tenant, env.ent, status="published", pricing_type="paid", price="100", currency="INR",
                          capacity="10", ticket_types=[dict(VIP)], modules={"registration": True, "tickets": True})

    def test_reproduces_the_report_then_the_explicit_enable_fixes_it(self, env):
        legacy = self._legacy_vip_event(env)
        assert legacy.default_ticket_type_id is None
        shown = client_for(env.db, env.owner).get(f"{API}/{legacy.id}").json()
        assert shown["default_ticket_type_id"] is None                     # what the Web developer saw
        assert [t["id"] for t in shown["ticket_types"]] == ["vip"]

        # nothing mutates it silently: reads, unrelated edits and re-sending the price leave it NULL
        event_service.update_event_service(env.db, legacy.id, EventUpdate(title="Edited"), env.owner)
        event_service.update_event_service(env.db, legacy.id, EventUpdate(price="100"), env.owner)
        assert stored(env, legacy.id).default_ticket_type_id is None

        assert event_service.enable_default_ticket_type(env.db, stored(env, legacy.id)) is True
        new_id = str(stored(env, legacy.id).default_ticket_type_id)
        assert client_for(env.db, env.owner).get(f"{API}/{legacy.id}").json()["default_ticket_type_id"] == new_id
        assert quote(env, stored(env, legacy.id), new_id).json()["ticket_subtotal"] == 100.0
        assert quote(env, stored(env, legacy.id), "vip").json()["ticket_subtotal"] == 150.0
        assert event_service.enable_default_ticket_type(env.db, stored(env, legacy.id)) is False   # never replaced
        assert str(stored(env, legacy.id).default_ticket_type_id) == new_id

    def test_enabling_never_touches_ticket_types_or_historical_orders(self, env):
        legacy = self._legacy_vip_event(env)
        assert checkout(env, legacy, "old@example.com", "vip").status_code == 201
        env.db.expire_all()
        order = env.db.query(EventOrder).one()
        reg = env.db.query(EventRegistration).one()
        before = (order.amount, order.currency, order.ticket_type_id, order.status, reg.ticket_type_id, reg.status)
        tickets_before = [dict(t) for t in stored(env, legacy.id).ticket_types]

        event_service.enable_default_ticket_type(env.db, stored(env, legacy.id))
        event_service.update_event_service(env.db, legacy.id, EventUpdate(price="300"), env.owner)
        env.db.expire_all()
        order, reg = env.db.query(EventOrder).one(), env.db.query(EventRegistration).one()
        assert (order.amount, order.currency, order.ticket_type_id, order.status, reg.ticket_type_id, reg.status) == before
        assert [dict(t) for t in stored(env, legacy.id).ticket_types] == tickets_before

    def test_enable_refuses_ineligible_events(self, env):
        no_price = make_event(env.db, env.tenant, env.ent, pricing_type="paid", price=None, currency="INR", ticket_types=[dict(VIP)])
        bad_currency = make_event(env.db, env.tenant, env.ent, pricing_type="paid", price="100", currency="RUPEES", ticket_types=[dict(VIP)])
        free = make_event(env.db, env.tenant, env.ent, pricing_type="free", price=None, ticket_types=[])
        disabled = make_event(env.db, env.tenant, env.ent, pricing_type="paid", price="100", currency="INR", ticket_types=[dict(VIP)],
                              form_configuration_version_id=disabled_price_version(env, enabled=False).id)
        for event in (no_price, bad_currency, free, disabled):
            assert event_service.enable_default_ticket_type(env.db, event) is False
            assert stored(env, event.id).default_ticket_type_id is None

    def test_duplicate_of_a_coexisting_event_gets_its_own_id(self, env):
        created = create(env, price="100", currency="INR", ticket_types=[dict(VIP)])
        clone = event_service.duplicate_event_service(env.db, created.id, env.owner)
        assert clone.default_ticket_type_id and clone.default_ticket_type_id != created.default_ticket_type_id


class TestBackfillScriptIsReadOnlyUntilTold:
    def test_list_eligible_reports_without_changing_anything(self, env, capsys):
        from scripts.enable_default_ticket_type import list_eligible

        eligible = make_event(env.db, env.tenant, env.ent, pricing_type="paid", price="100", currency="INR", ticket_types=[dict(VIP)])
        has_id = make_event(env.db, env.tenant, env.ent, pricing_type="paid", price="100", currency="INR", default_ticket_type_id=uuid4())
        free = make_event(env.db, env.tenant, env.ent, pricing_type="free", price=None)
        assert list_eligible(env.db) == 0
        out = capsys.readouterr().out
        assert str(eligible.id) in out and str(has_id.id) not in out and str(free.id) not in out
        assert "1 eligible" in out and "nothing was changed" in out
        for event in (eligible, free):
            assert stored(env, event.id).default_ticket_type_id is None
