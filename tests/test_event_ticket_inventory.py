"""Dynamic inventory on GET /events/{id} ``ticket_types[]``.

The detail API reuses checkout's seat definition (``event_service._seats_taken``): a seat is a confirmed/attended
registration, plus the extra seats of a confirmed multi-quantity order. Pending/failed/cancelled/waitlisted rows
never count. Inventory is read-only and advisory; checkout re-validates under the Event row lock.

Run:
    pytest tests/test_event_ticket_inventory.py -v
"""
import pytest

from app.models.event_aux_models import EventOrder, EventRegistration
from app.services import event_service
from event_sql_support import API, client_for, customer_user, make_event
from test_event_default_ticket import checkout, env, published, stored  # noqa: F401

VIP_A = {"id": "vip-a", "name": "VIP A", "price": "150", "capacity": 3}
VIP_B = {"id": "vip-b", "name": "VIP B", "price": "180", "capacity": 2}


def detail(env, event):
    r = client_for(env.db, customer_user("viewer@example.com")).get(f"{API}/{event.id}")
    assert r.status_code == 200, r.text
    return r.json()


def tickets(env, event):
    return {t["id"]: t for t in detail(env, event)["ticket_types"]}


def vip_event(env, capacity="10", ticket_types=None):
    return published(env, price="100", currency="INR", capacity=capacity,
                     ticket_types=[dict(VIP_A), dict(VIP_B)] if ticket_types is None else ticket_types)


def add_reg(env, event, ticket_id, status="confirmed", email=None):
    n = env.db.query(EventRegistration).count()
    env.db.add(EventRegistration(event_id=event.id, participant_name="P", participant_email=email or f"p{n}@x.com",
                                 ticket_type_id=ticket_id, status=status, qr_code=f"QR{n}"))
    env.db.commit()


def test_general_only_registrations_use_event_pool_not_ticket_sales(env):
    event = vip_event(env)
    assert checkout(env, event, "g@example.com", str(event.default_ticket_type_id)).status_code == 201
    d = detail(env, event)
    assert d["available_seats"] == 9
    for t in d["ticket_types"]:
        assert t["sold_count"] == 0 and t["remaining_capacity"] == t["capacity"] and t["sold_out"] is False
    assert {t["id"] for t in d["ticket_types"]} == {"vip-a", "vip-b"}  # default ticket never listed


def test_vip_a_sale_decreases_a_not_b_and_the_event_pool(env):
    event = vip_event(env)
    assert checkout(env, event, "v@example.com", "vip-a").status_code == 201
    d = detail(env, event)
    a, b = {t["id"]: t for t in d["ticket_types"]}["vip-a"], {t["id"]: t for t in d["ticket_types"]}["vip-b"]
    assert (a["capacity"], a["sold_count"], a["remaining_capacity"]) == (3, 1, 2)
    assert (b["capacity"], b["sold_count"], b["remaining_capacity"]) == (2, 0, 2)
    assert d["available_seats"] == 9 and d["is_full"] is False


def test_configured_capacity_is_never_mutated(env):
    event = vip_event(env)
    checkout(env, event, "v@example.com", "vip-a")
    assert [t["capacity"] for t in stored(env, event.id).ticket_types] == [3, 2]
    assert all("sold_count" not in t for t in stored(env, event.id).ticket_types)


def test_sold_out_at_zero_and_never_negative(env):
    event = vip_event(env)
    for i in range(2):
        assert checkout(env, event, f"b{i}@example.com", "vip-b").status_code == 201
    b = tickets(env, event)["vip-b"]
    assert b["remaining_capacity"] == 0 and b["sold_out"] is True and b["available_quantity"] == 0
    for i in range(3):  # oversell via direct inserts: still clamped
        add_reg(env, event, "vip-b")
    b = tickets(env, event)["vip-b"]
    assert b["sold_count"] == 5 and b["remaining_capacity"] == 0


def test_multi_quantity_order_counts_every_seat_once(env):
    event = vip_event(env)
    assert checkout(env, event, "q@example.com", "vip-a", quantity=2).status_code == 201
    d = detail(env, event)
    assert {t["id"]: t for t in d["ticket_types"]}["vip-a"]["sold_count"] == 2
    assert d["available_seats"] == 8


def test_unlimited_ticket_is_not_zero_and_is_capped_by_event_pool(env):
    event = vip_event(env, capacity="3", ticket_types=[{"id": "free-for-all", "name": "Open", "price": "50"},
                                                       {"id": "zero", "name": "Zero", "price": "50", "capacity": 0}])
    checkout(env, event, "a@example.com", "free-for-all")
    t = tickets(env, event)
    for key in ("free-for-all", "zero"):
        assert t[key]["remaining_capacity"] is None and t[key]["sold_out"] is False
        assert t[key]["available_quantity"] == 2  # event pool is the only limit


def test_effective_availability_is_min_of_ticket_and_event_remaining(env):
    event = vip_event(env, capacity="2")  # event pool 2, VIP A configured 3
    t = tickets(env, event)["vip-a"]
    assert t["remaining_capacity"] == 3 and t["available_quantity"] == 2
    checkout(env, event, "a@example.com", "vip-a")
    checkout(env, event, "b@example.com", "vip-a")
    d = detail(env, event)
    assert d["is_full"] is True
    a = {t["id"]: t for t in d["ticket_types"]}["vip-a"]
    assert a["remaining_capacity"] == 1 and a["sold_out"] is False and a["available_quantity"] == 0


@pytest.mark.parametrize("status", ["cancelled", "waitlisted", "payment_pending", "failed", "refunded", "no_show"])
def test_non_consuming_registration_states_do_not_count(env, status):
    event = vip_event(env)
    add_reg(env, event, "vip-a", status=status)
    t = tickets(env, event)["vip-a"]
    assert t["sold_count"] == 0 and t["remaining_capacity"] == 3
    assert detail(env, event)["available_seats"] == 10


def test_consuming_states_count_and_match_checkout(env):
    event = vip_event(env)
    add_reg(env, event, "vip-a", status="confirmed")
    add_reg(env, event, "vip-a", status="attended")
    assert tickets(env, event)["vip-a"]["sold_count"] == 2 == event_service._seats_taken(env.db, event.id, "vip-a")


def test_pending_or_failed_orders_do_not_inflate_sold_count(env):
    event = vip_event(env)
    for st in ("pending", "failed", "cancelled"):
        env.db.add(EventOrder(event_id=event.id, participant_name="P", participant_email=f"{st}@x.com",
                              ticket_type_id="vip-a", quantity="3", amount="450", currency="INR",
                              payment_status=st, status=st))
    env.db.commit()
    assert tickets(env, event)["vip-a"]["sold_count"] == 0
    assert detail(env, event)["available_seats"] == 10


def test_counts_are_scoped_to_their_event(env):
    event = vip_event(env)
    other = vip_event(env)
    checkout(env, other, "o@example.com", "vip-a")  # same ticket id, different event
    assert tickets(env, event)["vip-a"]["sold_count"] == 0
    assert tickets(env, other)["vip-a"]["sold_count"] == 1


def test_event_without_ticket_types_is_backward_compatible(env):
    event = published(env, price="100", currency="INR", capacity="5")
    checkout(env, event, "g@example.com", str(event.default_ticket_type_id))
    d = detail(env, event)
    assert not d["ticket_types"] and d["available_seats"] == 4
    assert d["default_ticket_type_id"] == str(event.default_ticket_type_id)


def test_meals_and_accommodation_unchanged_by_purchases(env):
    event = vip_event(env)
    before = detail(env, event)
    checkout(env, event, "v@example.com", "vip-a")
    after = detail(env, event)
    assert before["meals"] == after["meals"] and before["accommodation"] == after["accommodation"]


def test_detail_inventory_uses_two_grouped_queries_regardless_of_ticket_count(env):
    many = [{"id": f"t{i}", "name": f"T{i}", "price": "10", "capacity": 5} for i in range(8)]
    event = vip_event(env, ticket_types=many)
    row = stored(env, event.id)
    statements = []
    from sqlalchemy import event as sa_event
    engine = env.db.get_bind()
    sa_event.listen(engine, "before_cursor_execute", lambda *a: statements.append(a[2]))
    event_service.apply_event_inventory(env.db, row, {})
    assert len(statements) <= 2


def test_grouped_counter_matches_checkouts_seats_taken(env):
    event = vip_event(env, capacity="50")
    checkout(env, event, "a@example.com", "vip-a", quantity=3)
    checkout(env, event, "b@example.com", "vip-b")
    checkout(env, event, "c@example.com", str(event.default_ticket_type_id), quantity=2)
    for status in ("cancelled", "waitlisted", "attended"):
        add_reg(env, event, "vip-a", status=status)
    add_reg(env, event, None)
    total, by_ticket = event_service._seats_taken_grouped(env.db, event.id)
    assert total == event_service._seats_taken(env.db, event.id)
    for tid in ("vip-a", "vip-b", str(event.default_ticket_type_id)):
        assert by_ticket.get(tid, 0) == event_service._seats_taken(env.db, event.id, tid)
