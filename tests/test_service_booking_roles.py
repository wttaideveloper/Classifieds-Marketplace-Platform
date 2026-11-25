"""Service bookings for Providers (assigned appointments, attendance) and Super Admins (read-only overview across enterprises, totals, per-service counts).
Uses the fixture and helpers of test_service_bookings.py."""
from datetime import datetime, timedelta

from tests.test_service_bookings import API, book, booking_row, slot_start, w  # noqa: F401


def ids(resp):
    assert resp.status_code == 200, resp.text
    return [i["id"] for i in resp.json()["items"]]


# ---------------------------------------------------------------- Provider

def test_a_provider_sees_only_their_own_assigned_appointments(w):
    mine = book(w, w.people.priya, w.make_service(max_participants=3)).json()                       # assigned to Coach Alex
    nobodys = book(w, w.people.ravi, w.make_service("No provider", provider=False)).json()
    foreign = w.make_service("Other business", enterprise=w.other_ent, tenant_id=w.other_tenant)    # same provider id, another tenant
    elsewhere = book(w, w.people.ravi, foreign, hour=11).json()

    coach = w.as_user(w.people.coach).get(f"{API}/assigned")

    assert ids(coach) == [mine["id"]] and coach.json()["total"] == 1
    item = coach.json()["items"][0]
    assert item["reference"] == mine["reference"] and item["customer"]["name"] == "Priya Sharma" and item["timezone"] == "Asia/Kolkata"
    assert item["venue"]["name"] == "Spin Health Clinic" and item["duration_minutes"] == 60 and item["allowed_statuses"] == ["in_progress", "completed", "no_show"]
    assert elsewhere["id"] not in ids(coach) and nobodys["id"] not in ids(coach)
    assert ids(w.as_user(w.people.other_coach).get(f"{API}/assigned")) == []
    for user in (w.people.admin, w.people.priya, w.people.root):
        assert w.as_user(user).get(f"{API}/assigned").json()["detail"]["code"] == "PROVIDER_ONLY"


def test_assigned_appointments_filter_by_date_status_and_search(w):
    service = w.make_service(max_participants=5)
    soon = book(w, w.people.priya, service, offset=2).json()
    later = book(w, w.people.ravi, service, offset=5).json()
    w.as_user(w.people.ravi).post(f"{API}/{later['id']}/cancel", json={})
    coach = w.as_user(w.people.coach)

    day = soon["starts_at"][:10]
    assert ids(coach.get(f"{API}/assigned", params={"date_from": day, "date_to": day})) == [soon["id"]]
    assert ids(coach.get(f"{API}/assigned", params={"status": "cancelled"})) == [later["id"]]
    assert ids(coach.get(f"{API}/assigned", params={"q": "ravi"})) == [later["id"]]
    assert ids(coach.get(f"{API}/assigned", params={"when": "upcoming"})) == [soon["id"]]           # a cancelled one is not upcoming


def test_a_provider_runs_attendance_but_cannot_cancel_or_touch_payments(w):
    service = w.make_service(price=200)
    b = book(w, w.people.priya, service).json()
    coach = w.as_user(w.people.coach)

    assert coach.post(f"{API}/{b['id']}/payment", json={}).status_code == 403                            # cannot record a payment
    w.as_user(w.people.admin).post(f"{API}/{b['id']}/payment", json={})
    coach = w.as_user(w.people.coach)                                                                  # one shared client: switch back to the Provider
    early = coach.patch(f"{API}/{b['id']}/status", json={"status": "in_progress"})
    assert early.status_code == 409 and early.json()["detail"]["code"] == "NOT_STARTED"                   # too early to start
    assert coach.patch(f"{API}/{b['id']}/status", json={"status": "no_show"}).json()["detail"]["code"] == "NOT_STARTED"
    refused = coach.patch(f"{API}/{b['id']}/status", json={"status": "cancelled", "reason": "Cannot make it"})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "INVALID_TRANSITION"           # cancelling is not a Provider action
    row = booking_row(w, b["id"])
    row.starts_at, row.ends_at = datetime.utcnow() - timedelta(minutes=5), datetime.utcnow() + timedelta(minutes=55)
    w.db.commit()
    assert coach.patch(f"{API}/{b['id']}/status", json={"status": "in_progress"}).json()["status"] == "in_progress"
    assert coach.patch(f"{API}/{b['id']}/status", json={"status": "completed"}).json()["status"] == "completed"
    paid_flag = coach.post(API, json={"service_id": str(service.id), "starts_at": slot_start(w, service, hour=11), "customer_name": "Walk In", "paid": True})
    assert paid_flag.json()["detail"]["code"] == "PROVIDER_CANNOT_RECORD_PAYMENT"


# ---------------------------------------------------------------- Super Admin

def test_the_super_admin_lists_bookings_across_enterprises_read_only(w):
    here = book(w, w.people.priya, w.make_service(max_participants=3)).json()
    there = book(w, w.people.ravi, w.make_service("Other business", enterprise=w.other_ent, tenant_id=w.other_tenant, provider=False)).json()
    root = w.as_user(w.people.root)

    every = root.get(f"{API}/admin")
    assert sorted(ids(every)) == sorted([here["id"], there["id"]]) and every.json()["total"] == 2
    assert {i["enterprise_name"] for i in every.json()["items"]} == {"Spin Health", "Other"}
    assert ids(root.get(f"{API}/admin", params={"enterprise_id": str(w.other_ent.id)})) == [there["id"]]
    assert ids(root.get(f"{API}/admin", params={"q": "spin health"})) == [here["id"]]                      # search by enterprise name too
    assert ids(root.get(f"{API}/admin", params={"source": "manual"})) == []
    paged = root.get(f"{API}/admin", params={"page": 2, "page_size": 1}).json()
    assert paged["total"] == 2 and len(paged["items"]) == 1
    for user in (w.people.admin, w.people.coach, w.people.priya):
        assert w.as_user(user).get(f"{API}/admin").json()["detail"]["code"] == "SUPER_ADMIN_ONLY"
    root = w.as_user(w.people.root)
    detail = root.get(f"{API}/{there['id']}")
    assert detail.status_code == 200 and detail.json()["enterprise_id"] == str(w.other_ent.id)
    assert [h["action"] for h in root.get(f"{API}/{there['id']}/history").json()] == ["created"]


def test_the_super_admin_gets_totals_and_counts_per_service(w):
    free = w.make_service(max_participants=5)
    paid = w.make_service("Paid", price=100, provider=False)
    foreign = w.make_service("Other business", enterprise=w.other_ent, tenant_id=w.other_tenant, provider=False)
    book(w, w.people.priya, free)
    gone = book(w, w.people.ravi, free, hour=11).json()
    w.as_user(w.people.ravi).post(f"{API}/{gone['id']}/cancel", json={})
    book(w, w.people.priya, paid)
    book(w, w.people.ravi, foreign)
    root = w.as_user(w.people.root)

    summary = root.get(f"{API}/admin/summary").json()
    assert summary["total"] == 4 and summary["by_stage"] == {"confirmed": 2, "cancelled": 1, "pending_payment": 1}
    assert summary["exceptions"] == {"refund_pending": 0, "refund_failed": 0, "legacy_pending_approval": 0}
    assert root.get(f"{API}/admin/summary", params={"enterprise_id": str(w.other_ent.id)}).json()["total"] == 1

    unused = w.make_service("Unused", provider=False)
    counts = {c["service_id"]: c for c in root.get(f"{API}/admin/service-counts", params={"service_ids": f"{free.id},{paid.id},{unused.id}"}).json()["items"]}
    assert counts[str(free.id)]["total"] == 2 and counts[str(free.id)]["by_stage"] == {"confirmed": 1, "cancelled": 1}
    assert counts[str(paid.id)]["by_stage"] == {"pending_payment": 1} and counts[str(unused.id)]["total"] == 0
    assert root.get(f"{API}/admin/service-counts", params={"service_ids": "nope"}).json()["detail"]["code"] == "INVALID_SERVICE_IDS"
    assert w.as_user(w.people.admin).get(f"{API}/admin/summary").json()["detail"]["code"] == "SUPER_ADMIN_ONLY"
    assert w.as_user(w.people.admin).get(f"{API}/admin/service-counts", params={"service_ids": str(free.id)}).status_code == 403
