"""Service bookings, revised Booking & Scheduling document: blocked dates, per-service notice, references, booking source, notifications, refund labelling,
the capacity guard and the dashboard summary. Uses the fixture and helpers of test_service_bookings.py."""
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from tests.test_service_bookings import API, book, booking_row, categories, day_of, person, slot_start, w  # noqa: F401


def slots_of(w, service, offset=3):
    resp = w.client.get(f"/api/v1/services/{service.id}/available-slots", params={"date": day_of(offset).isoformat()})
    return {s["starts_at"][11:16]: s for s in resp.json()["days"][0]["slots"]}


def test_blocked_days_and_time_ranges_cannot_be_booked_by_anyone(w):
    day = day_of(3).isoformat()
    service = w.make_service(blocked_periods=[{"start_date": day, "end_date": day, "start_time": "10:00", "end_time": "11:00", "reason": "Staff meeting"}])
    whole = w.make_service("Closed", provider=False, blocked_periods=[{"start_date": day, "reason": "Holiday"}])

    shown = slots_of(w, service)
    assert shown["10:00"]["reason"] == "blocked" and shown["10:00"]["available"] is False and shown["09:00"]["available"] is True
    assert all(s["reason"] == "blocked" for s in slots_of(w, whole).values())
    starts = shown["10:00"]["starts_at"]
    assert w.as_user(w.people.priya).post(API, json={"service_id": str(service.id), "starts_at": starts}).json()["detail"]["code"] == "SLOT_BLOCKED"
    manual = w.as_user(w.people.admin).post(API, json={"service_id": str(service.id), "starts_at": starts, "customer_name": "Walk In"})
    assert manual.json()["detail"]["code"] == "SLOT_BLOCKED"                                   # the same engine for manual bookings
    assert book(w, w.people.priya, service, hour=9).status_code == 201


def test_each_service_can_have_its_own_notice_window(w):
    near = w.make_service(min_notice_minutes=60 * 24 * 5)                      # 5 days' notice
    far = w.make_service("Short horizon", provider=False, max_advance_days=1)

    assert all(s["reason"] == "too_soon" for s in slots_of(w, near).values())
    assert all(s["reason"] == "too_far" for s in slots_of(w, far).values())
    assert slots_of(w, w.make_service("Default", provider=False))["10:00"]["available"] is True


def test_a_booking_has_a_searchable_reference_and_a_source(w):
    service = w.make_service(max_participants=3)
    mine = book(w, w.people.priya, service).json()
    walk_in = w.as_user(w.people.admin).post(API, json={"service_id": str(service.id), "starts_at": slot_start(w, service, hour=11), "customer_name": "Walk In",
                                                        "customer_phone": "+91 98480 22338"}).json()
    admin = w.as_user(w.people.admin)
    url = f"/api/v1/enterprises/{w.ent.id}/service-bookings"

    assert mine["reference"] == "SB-" + mine["id"].replace("-", "")[:10].upper() and mine["source"] == "customer" and walk_in["source"] == "manual"
    assert [i["id"] for i in admin.get(url, params={"q": mine["reference"].lower()}).json()["items"]] == [mine["id"]]
    assert [i["id"] for i in admin.get(url, params={"q": "98480"}).json()["items"]] == [walk_in["id"]]
    assert [i["id"] for i in admin.get(url, params={"source": "manual"}).json()["items"]] == [walk_in["id"]]
    assert [i["id"] for i in admin.get(url, params={"source": "customer"}).json()["items"]] == [mine["id"]]
    assert admin.get(url, params={"delivery_mode": "virtual"}).json()["items"] == []
    assert admin.get(url, params={"source": "bogus"}).status_code == 422


def test_a_manual_booking_tells_the_customer_and_the_provider_but_not_the_admin_who_made_it(w):
    service = w.make_service()
    w.sent.clear()

    w.as_user(w.people.admin).post(API, json={"service_id": str(service.id), "starts_at": slot_start(w, service), "customer_user_id": w.people.priya["id"], "customer_name": "Priya"})

    to_customer, to_staff = w.sent
    assert to_customer["category"] == "service_booking_confirmed" and "Spin Health has booked" in to_customer["message"]
    assert to_staff["category"] == "service_booking_new" and {str(u) for u in to_staff["user_ids"]} == {w.people.coach["id"]}


def test_a_merchant_cancellation_tells_the_provider_and_says_who_cancelled(w):
    service = w.make_service(max_participants=3)
    b = book(w, w.people.priya, service).json()
    w.sent.clear()

    done = w.as_user(w.people.admin).patch(f"{API}/{b['id']}/status", json={"status": "cancelled", "reason": "The clinic is closed"}).json()

    assert done["cancellation"]["cancelled_by"] == "merchant" and done["refund_status"] is None and done["cancellation"]["refund_status"] is None
    assert [(n["category"], {str(u) for u in n["user_ids"]}) for n in w.sent] == [
        ("service_booking_cancelled", {w.people.priya["id"]}), ("service_booking_cancelled", {w.people.coach["id"]})]
    own = book(w, w.people.ravi, service, hour=11).json()
    assert w.as_user(w.people.ravi).post(f"{API}/{own['id']}/cancel", json={}).json()["cancellation"]["cancelled_by"] == "customer"


def test_booking_payment_and_refund_statuses_are_independent(w, monkeypatch):
    service = w.make_service(price=400, max_participants=3)
    b = book(w, w.people.priya, service).json()
    assert (b["stage"], b["payment_status"], b["refund_status"]) == ("pending_payment", "unpaid", None)
    paid = w.as_user(w.people.admin).post(f"{API}/{b['id']}/payment", json={}).json()
    assert (paid["status"], paid["payment_status"], paid["refund_status"]) == ("confirmed", "paid", None)

    done = w.as_user(w.people.admin).patch(f"{API}/{b['id']}/status", json={"status": "cancelled", "reason": "Provider unwell"}).json()

    # cancelled, still paid (no gateway refund has happened), refund owed: three separate answers
    assert (done["status"], done["payment_status"], done["refund_status"]) == ("cancelled", "paid", "pending")
    assert done["cancellation"]["cancelled_by"] == "merchant" and done["cancellation"]["refund_amount"] == 400
    listed = w.as_user(w.people.admin).get(f"/api/v1/enterprises/{w.ent.id}/service-bookings", params={"refund_status": "pending"}).json()
    assert [i["id"] for i in listed["items"]] == [b["id"]]

    from app.core.config import settings
    monkeypatch.setattr(settings, "SERVICE_BOOKING_MERCHANT_REFUND_PERCENT", 50)      # the merchant refund default is a setting, not a hardcoded 100
    other = book(w, w.people.ravi, service, hour=11).json()
    w.as_user(w.people.admin).post(f"{API}/{other['id']}/payment", json={})
    half = w.as_user(w.people.admin).patch(f"{API}/{other['id']}/status", json={"status": "cancelled", "reason": "Provider unwell"}).json()
    assert half["cancellation"]["refund_percent"] == 50 and half["cancellation"]["refund_amount"] == 200 and half["refund_status"] == "pending"


def test_a_free_cancelled_booking_owes_no_refund(w):
    b = book(w, w.people.priya, w.make_service()).json()
    done = w.as_user(w.people.priya).post(f"{API}/{b['id']}/cancel", json={}).json()
    assert (done["status"], done["payment_status"], done["refund_status"]) == ("cancelled", "not_required", None)


def test_an_expired_payment_hold_tells_the_customer(w):
    from app.services import service_booking_service as svc

    b = book(w, w.people.priya, w.make_service(price=100, provider=False)).json()
    row = booking_row(w, b["id"])
    row.hold_expires_at = datetime.utcnow() - timedelta(seconds=1)
    w.db.commit()

    svc.expire_stale_holds(w.db)

    assert categories(w)[-1] == "service_booking_expired" and {str(u) for u in w.sent[-1]["user_ids"]} == {w.people.priya["id"]}


def test_capacity_cannot_be_lowered_below_the_seats_already_booked(w):
    from app.schemas.service_schema import ServiceUpdate
    from app.services.service_service import _check_capacity_not_below_bookings

    service = w.make_service(max_participants=3, provider=False)
    book(w, w.people.priya, service, participants=2)

    with pytest.raises(HTTPException) as refused:
        _check_capacity_not_below_bookings(w.db, service, ServiceUpdate(max_participants=1))
    assert refused.value.status_code == 409 and refused.value.detail["code"] == "CAPACITY_BELOW_BOOKINGS" and refused.value.detail["booked"] == 2
    _check_capacity_not_below_bookings(w.db, service, ServiceUpdate(max_participants=2))            # equal is fine
    _check_capacity_not_below_bookings(w.db, service, ServiceUpdate(service_name="Renamed"))          # capacity not touched


def test_blocked_periods_are_validated(w):
    from pydantic import ValidationError

    from app.schemas.service_schema import ServiceUpdate

    for bad in ({"start_date": "2026-12-10", "end_date": "2026-12-09"}, {"start_date": "2026-12-10", "start_time": "10:00"},
                {"start_date": "2026-12-10", "start_time": "11:00", "end_time": "10:00"}, {"start_date": "2026-12-10", "start_time": "25:00", "end_time": "26:00"}):
        with pytest.raises(ValidationError):
            ServiceUpdate(blocked_periods=[bad])
    ok = ServiceUpdate(blocked_periods=[{"start_date": "2026-12-25", "reason": "Christmas"}]).to_model_data()
    assert ok["blocked_periods"] == [{"start_date": "2026-12-25", "end_date": None, "start_time": None, "end_time": None, "reason": "Christmas"}]


def test_the_dashboard_summary_counts_by_stage(w):
    free, paid = w.make_service(max_participants=5), w.make_service("Paid", price=100, provider=False)
    book(w, w.people.priya, free)
    book(w, w.people.ravi, paid)

    summary = w.as_user(w.people.admin).get(f"/api/v1/enterprises/{w.ent.id}/service-bookings/summary").json()

    assert summary["upcoming"] == 1 and summary["pending_payment"] == 1 and summary["change_requests"] == 0 and summary["legacy_pending_approval"] == 0
    assert w.as_user(w.people.other_admin).get(f"/api/v1/enterprises/{w.ent.id}/service-bookings/summary").status_code == 404


def test_the_impact_of_an_availability_change_lists_affected_bookings_and_changes_nothing(w):
    service = w.make_service(max_participants=3, provider=False)
    at_ten = book(w, w.people.priya, service, hour=10, participants=2).json()
    at_eleven = book(w, w.people.ravi, service, hour=11).json()
    held = book(w, w.people.priya, w.make_service("Paid", price=100, provider=False), hour=10).json()
    day = day_of(3).isoformat()
    admin = w.as_user(w.people.admin)

    impact = admin.post(f"/api/v1/services/{service.id}/availability-impact", json={
        "blocked_periods": [{"start_date": day, "start_time": "10:00", "end_time": "11:00"}], "max_participants": 1}).json()

    assert impact["affected_count"] == 1
    [row] = impact["bookings"]
    assert row["id"] == at_ten["id"] and sorted(row["reasons"]) == ["blocked", "over_capacity"] and row["reference"] == at_ten["reference"]
    week = [{"day": d, "is_available": True, "start_time": "09:00", "end_time": "11:00", "slot_length": "60"}
            for d in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")]
    shorter = admin.post(f"/api/v1/services/{service.id}/availability-impact", json={"availability": week}).json()
    assert [(r["id"], r["reasons"]) for r in shorter["bookings"]] == [(at_eleven["id"], ["outside_schedule"])]
    held_service = held["service_id"]
    holds = admin.post(f"/api/v1/services/{held_service}/availability-impact", json={"blocked_periods": [{"start_date": day}]}).json()
    assert holds["bookings"][0]["stage"] == "pending_payment"                                  # payment holds are listed too
    assert booking_row(w, at_ten["id"]).status == "confirmed"                                   # nothing was changed
    assert w.as_user(w.people.coach).post(f"/api/v1/services/{service.id}/availability-impact", json={}).status_code == 403
    assert w.as_user(w.people.other_admin).post(f"/api/v1/services/{service.id}/availability-impact", json={}).status_code == 403


def test_reminders_are_never_sent_for_cancelled_or_expired_bookings(w):
    from app.services import service_booking_notifications as notify

    service = w.make_service(max_participants=5)
    keep, gone, lapsed = (book(w, u, service, hour=9).json() for u in (w.people.priya, w.people.ravi, person("Meena Rao", "customer")))
    w.as_user(w.people.ravi).post(f"{API}/{gone['id']}/cancel", json={})
    for booking_id in (keep["id"], gone["id"], lapsed["id"]):
        row = booking_row(w, booking_id)
        row.starts_at, row.ends_at = datetime.utcnow() + timedelta(minutes=30), datetime.utcnow() + timedelta(minutes=90)
        if booking_id == lapsed["id"]:
            row.status = "expired"
        w.db.commit()
    w.sent.clear()

    notify.send_due_reminders(w.db)

    assert [n["metadata"]["booking_id"] for n in w.sent] == [keep["id"]]
