"""Service bookings: slots, booking, capacity per slot, provider overlap, holds and payment status, approval, cancellation (instant and request),
admin status changes, enterprise isolation, history, meeting / venue, notifications and reminders. Real routes on an in-memory database.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import service_booking as endpoints
from app.core.catalog_access import CatalogAccess, get_catalog_access
from app.core.config import settings
from app.core.dependencies import get_current_user
from app.db.database import Base, get_db
from app.models.enterprise_model import Enterprise
from app.models.location_model import EnterpriseLocation
from app.models.notification_model import Notification, NotificationEventLog, NotificationLog, UserNotification
from app.models.service_booking_model import ServiceBooking, ServiceBookingEvent
from app.models.service_model import Service
from app.services import service_booking_notifications as notify
from app.services import service_booking_slots as slot_rules

TZ = ZoneInfo("Asia/Kolkata")
TABLES = [Enterprise, EnterpriseLocation, Service, ServiceBooking, ServiceBookingEvent, Notification, UserNotification, NotificationLog, NotificationEventLog]
WEEK = [{"day": d, "is_available": True, "start_time": "09:00", "end_time": "12:00", "slot_length": "60"}
        for d in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")]


def person(name, role, tenant=None):
    out = {"id": str(uuid4()), "role": role, "name": name, "email": f"{name.lower().replace(' ', '.')}@example.test"}
    if tenant:
        out["tenant_id"] = str(tenant)
    return out


@pytest.fixture
def w(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[m.__table__ for m in TABLES])
    db = sessionmaker(bind=engine)()
    tenant, other_tenant = uuid4(), uuid4()
    ent = Enterprise(tenant_id=tenant, business_short_name="Spin Health", business_legal_name="Spin Health Ltd", business_email="s@example.test", status="approved")
    other_ent = Enterprise(tenant_id=other_tenant, business_short_name="Other", business_legal_name="Other Ltd", business_email="o@example.test", status="approved")
    db.add_all([ent, other_ent])
    db.commit()
    place = EnterpriseLocation(enterprise_id=ent.id, location_name="Spin Health Clinic", address_line_1="12 Road No 1", city="Hyderabad", latitude=17.38, longitude=78.48)
    db.add(place)
    db.commit()

    people = SimpleNamespace(
        priya=person("Priya Sharma", "customer"), ravi=person("Ravi Kumar", "customer"), admin=person("Clinic Admin", "admin", tenant),
        other_admin=person("Other Admin", "admin", other_tenant), coach=person("Coach Alex", "provider", tenant), other_coach=person("Coach Sam", "provider", tenant),
        root={"id": str(uuid4()), "role": "super_admin", "name": "Root", "email": "root@example.test", "status": "active"},
    )
    state = {"user": people.priya}
    app = FastAPI()
    app.include_router(endpoints.service_router, prefix="/api/v1/services")
    app.include_router(endpoints.router, prefix="/api/v1/service-bookings")
    app.include_router(endpoints.me_router, prefix="/api/v1/users/me/service-bookings")
    app.include_router(endpoints.enterprise_router, prefix="/api/v1/enterprises")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: state["user"]

    def access():
        u = state["user"]
        role = u["role"]
        tenant_id = None if role in ("customer", "super_admin") else __import__("uuid").UUID(u["tenant_id"])
        return CatalogAccess(role, tenant_id, __import__("uuid").UUID(u["id"]) if role == "provider" else None)

    app.dependency_overrides[get_catalog_access] = access
    client = TestClient(app, raise_server_exceptions=False)

    sent, claimed = [], set()
    monkeypatch.setattr("app.services.notification_service.create_automatic_notification", lambda _db, **kw: sent.append(kw) or SimpleNamespace(id=uuid4()))
    monkeypatch.setattr(notify.notification_idempotency, "claim", lambda _db, key: key not in claimed and not claimed.add(key))
    monkeypatch.setattr(notify.notification_idempotency, "release", lambda _db, key: claimed.discard(key))
    monkeypatch.setattr("app.services.event_notification_service.resolve_enterprise_admin_user_ids", lambda _db, item, access_token=None: ([__import__("uuid").UUID(people.admin["id"])], tenant))

    def as_user(user):
        state["user"] = user
        return client

    def make_service(name="Kids Fitness Program", price=0.0, max_participants=None, provider=True, enterprise=ent, tenant_id=tenant, **extra):
        values = dict(
            tenant_id=tenant_id, enterprise_id=enterprise.id, location_id=place.id if enterprise is ent else None, service_name=name, service_category="Fitness",
            service_price=price, duration=60, status="active", service_status=True, availability_status=True, availability_schedule=WEEK, max_participants=max_participants,
            provider_user_id=__import__("uuid").UUID(people.coach["id"]) if provider else None, provider_name="Coach Alex" if provider else None, currency="INR",
            delivery_modes=["in_person"], time_zone="Asia/Kolkata", venue_instructions="Reception, 1st floor",
        )
        values.update(extra)
        service = Service(**values)
        db.add(service)
        db.commit()
        return service

    ns = SimpleNamespace(db=db, client=client, as_user=as_user, people=people, ent=ent, other_ent=other_ent, tenant=tenant, other_tenant=other_tenant, sent=sent,
                         make_service=make_service, place=place)
    yield ns
    db.close()
    engine.dispose()


API = "/api/v1/service-bookings"


def day_of(offset=3):
    return (datetime.now(TZ) + timedelta(days=offset)).date()


def slot_start(w, service, hour=10, offset=3):
    """The `starts_at` the slots endpoint offers for that local hour."""
    day = day_of(offset)
    body = w.client.get(f"/api/v1/services/{service.id}/available-slots", params={"date": day.isoformat()}).json()
    return next(s["starts_at"] for s in body["days"][0]["slots"] if s["starts_at"].startswith(f"{day.isoformat()}T{hour:02d}:00"))


def book(w, user, service, hour=10, offset=3, key=None, **body):
    headers = {"Idempotency-Key": key} if key else {}
    return w.as_user(user).post(API, json={"service_id": str(service.id), "starts_at": slot_start(w, service, hour, offset), **body}, headers=headers)


def booking_row(w, booking_id):
    w.db.expire_all()
    return w.db.get(ServiceBooking, __import__("uuid").UUID(booking_id))


def categories(w):
    return [n["category"] for n in w.sent]


# =============================================================================================================
# 1. the service settings and the slots
# =============================================================================================================

def test_the_new_service_fields_are_accepted_and_the_public_answer_never_has_the_meeting_link():
    from app.schemas.service_schema import ServiceCreate, ServiceResponse

    data = ServiceCreate(enterprise_id=uuid4(), service_name="Yoga", category="Fitness", duration_minutes=60, price=0, delivery_modes=["virtual"], requires_approval=True,
                         time_zone="Asia/Kolkata", cancellation_rules={"free_cancellation_hours": 12}, meeting_url="https://meet.example/abc", meeting_passcode="1234").to_model_data()

    assert data["delivery_modes"] == ["virtual"] and data["requires_approval"] is True and data["meeting_url"] == "https://meet.example/abc"
    assert data["cancellation_rules"]["free_cancellation_hours"] == 12 and data["cancellation_rules"]["refund_percent_before"] == 100
    assert not {"meeting_url", "meeting_passcode", "meeting_provider", "meeting_instructions"} & set(ServiceResponse.model_fields)
    with pytest.raises(ValueError):
        ServiceCreate(enterprise_id=uuid4(), service_name="Yoga", category="Fitness", duration_minutes=60, price=0, time_zone="Mars/Base")


def test_slots_come_from_the_weekly_schedule_in_the_services_zone(w):
    service = w.make_service()
    day = day_of(3)

    body = w.client.get(f"/api/v1/services/{service.id}/available-slots", params={"date": day.isoformat(), "timezone": "UTC"}).json()

    assert body["bookable"] is True and body["time_zone"] == "Asia/Kolkata" and body["duration_minutes"] == 60 and body["capacity"] == 1
    slots = body["days"][0]["slots"]
    assert [s["starts_at"] for s in slots] == [f"{day}T09:00:00+05:30", f"{day}T10:00:00+05:30", f"{day}T11:00:00+05:30"]
    assert slots[0]["ends_at"] == f"{day}T10:00:00+05:30" and slots[0]["starts_at_display"] == f"{day}T03:30:00+00:00"
    assert all(s["available"] and s["available_seats"] == 1 and s["reason"] is None for s in slots)
    assert body["provider_name"] == "Coach Alex" and body["delivery_modes"] == ["in_person"] and body["hold_minutes"] == 15


def test_slots_too_soon_are_marked_and_ranges_are_limited(w):
    service = w.make_service()
    today = datetime.now(TZ).date()

    now_slots = w.client.get(f"/api/v1/services/{service.id}/available-slots", params={"date": today.isoformat()}).json()["days"][0]["slots"]
    assert all(s["reason"] == "too_soon" for s in now_slots if datetime.fromisoformat(s["starts_at"]) < datetime.now(TZ) + timedelta(hours=1))
    assert w.client.get(f"/api/v1/services/{service.id}/available-slots", params={"from": today.isoformat(), "to": (today + timedelta(days=40)).isoformat()}).status_code == 422
    assert w.client.get(f"/api/v1/services/{service.id}/available-slots", params={"date": today.isoformat(), "timezone": "Mars/Base"}).status_code == 422
    week = w.client.get(f"/api/v1/services/{service.id}/available-slots").json()
    assert len(week["days"]) == 7


def test_a_service_that_is_not_bookable_has_no_slots_or_is_not_found(w):
    draft = w.make_service("Draft", status="draft")
    off = w.make_service("Switched off", booking_enabled=False)

    assert w.client.get(f"/api/v1/services/{draft.id}/available-slots").status_code == 404
    body = w.client.get(f"/api/v1/services/{off.id}/available-slots", params={"date": day_of(3).isoformat()}).json()
    assert body["bookable"] is False and "switched off" in body["reason"] and body["days"][0]["slots"] == []
    assert w.client.get(f"/api/v1/services/{uuid4()}/available-slots").status_code == 404
    resp = book(w, w.people.priya, w.make_service("Open"), hour=10)
    assert resp.status_code == 201
    refused = w.as_user(w.people.priya).post(API, json={"service_id": str(off.id), "starts_at": f"{day_of(3)}T10:00:00+05:30"})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "SERVICE_NOT_BOOKABLE"


# =============================================================================================================
# 2. booking
# =============================================================================================================

def test_a_free_service_is_confirmed_at_once_with_the_full_detail(w):
    service = w.make_service()

    resp = book(w, w.people.priya, service)

    assert resp.status_code == 201, resp.text
    b = resp.json()
    day = day_of(3)
    assert b["status"] == "confirmed" and b["payment_status"] == "not_required" and b["delivery_mode"] == "in_person"
    assert b["starts_at"] == f"{day}T10:00:00+05:30" and b["ends_at"] == f"{day}T11:00:00+05:30" and b["timezone"] == "Asia/Kolkata"
    assert b["service_name"] == "Kids Fitness Program" and b["provider_name"] == "Coach Alex" and b["enterprise_name"] == "Spin Health"
    assert b["venue"] == {"name": "Spin Health Clinic", "line1": "12 Road No 1", "line2": None, "city": "Hyderabad", "state": None, "postal_code": None, "country": None,
                          "lat": 17.38, "lng": 78.48, "phone": None, "instructions": "Reception, 1st floor"}
    assert b["meeting"] is None and b["can_cancel"] is True and b["cancel_mode"] == "instant" and b["can_reschedule"] is False and b["can_join"] is False
    assert b["can_check_in"] is False and b["payment_id"] is None and b["qr_payload"] is None and "Free cancellation until 24 hours" in b["cancellation_policy_summary"]
    assert [h["action"] for h in b["history"]] == ["created"] and b["history"][0]["actor_role"] == "customer" and b["history"][0]["actor_name"] is None
    [note] = [n for n in w.sent if n["category"] == "service_booking_confirmed"]
    assert note["user_ids"] == [__import__("uuid").UUID(w.people.priya["id"])] and note["channels"] == ["in_app", "push"]
    assert note["metadata"] == {"category": "service_booking_confirmed", "booking_id": b["id"], "service_id": str(service.id), "service_name": "Kids Fitness Program",
                                "entity_type": "service_booking", "entity_id": b["id"], "booking_reference": b["reference"], "starts_at": b["starts_at"], "delivery_mode": "in_person", "status": "confirmed"}


def test_bad_requests_are_refused_with_a_code(w):
    service = w.make_service()
    day = day_of(3)

    def attempt(**body):
        return w.as_user(w.people.priya).post(API, json={"service_id": str(service.id), **body})

    no_offset = attempt(starts_at=f"{day}T10:00:00")
    assert no_offset.status_code == 422 and no_offset.json()["detail"]["code"] == "INVALID_STARTS_AT" and "UTC offset" in no_offset.json()["detail"]["message"]
    assert attempt(starts_at=f"{day}T10:30:00+05:30").json()["detail"]["code"] == "SLOT_NOT_OFFERED"                 # not on the slot grid
    assert attempt(starts_at=f"{day}T10:00:00+05:30", provider_id=str(uuid4())).json()["detail"]["code"] == "PROVIDER_NOT_FOR_SERVICE"
    assert attempt(starts_at=f"{day}T10:00:00+05:30", delivery_mode="virtual").json()["detail"]["code"] == "DELIVERY_MODE_NOT_OFFERED"
    assert attempt(starts_at=f"{day}T10:00:00+05:30", customer_user_id=str(uuid4())).json()["detail"]["code"] == "BOOK_FOR_SELF_ONLY"
    past = attempt(starts_at=f"{datetime.now(TZ).date() - timedelta(days=1)}T10:00:00+05:30")
    assert past.status_code == 409 and past.json()["detail"]["code"] == "SLOT_IN_THE_PAST"
    two_ways = w.make_service("Two ways", delivery_modes=["in_person", "virtual"])
    resp = w.as_user(w.people.priya).post(API, json={"service_id": str(two_ways.id), "starts_at": slot_start(w, two_ways)})
    assert resp.status_code == 422 and resp.json()["detail"]["code"] == "DELIVERY_MODE_REQUIRED" and resp.json()["detail"]["delivery_modes"] == ["in_person", "virtual"]
    assert book(w, w.people.priya, two_ways, delivery_mode="virtual").json()["delivery_mode"] == "virtual"


def test_capacity_is_per_time_slot_and_cancelling_releases_it(w):
    service = w.make_service(max_participants=2, provider=False)
    first = book(w, w.people.priya, service)
    second = book(w, w.people.ravi, service)
    assert first.status_code == second.status_code == 201

    full = w.as_user(w.people.admin).post(API, json={"service_id": str(service.id), "starts_at": slot_start(w, service), "customer_name": "Walk In"})
    assert full.status_code == 409 and full.json()["detail"]["code"] == "SLOT_FULL" and full.json()["detail"]["available_seats"] == 0
    day = day_of(3)
    slots = w.client.get(f"/api/v1/services/{service.id}/available-slots", params={"date": day.isoformat()}).json()["days"][0]["slots"]
    assert [(s["available_seats"], s["reason"]) for s in slots] == [(2, None), (0, "full"), (2, None)]                # only the 10:00 slot is taken

    assert w.as_user(w.people.priya).post(f"{API}/{first.json()['id']}/cancel", json={}).json()["status"] == "cancelled"
    again = w.as_user(w.people.admin).post(API, json={"service_id": str(service.id), "starts_at": slot_start(w, service), "customer_name": "Walk In"})
    assert again.status_code == 201


def test_a_party_cannot_be_bigger_than_the_seats_left(w):
    service = w.make_service(max_participants=3, provider=False)
    assert book(w, w.people.priya, service, participants=2).status_code == 201

    resp = book(w, w.people.ravi, service, participants=2)

    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "SLOT_FULL" and resp.json()["detail"]["available_seats"] == 1
    assert book(w, w.people.ravi, service, participants=1).status_code == 201


def test_a_provider_cannot_be_booked_twice_across_services_but_shares_a_group_session(w):
    group = w.make_service("Group class", max_participants=5)
    other = w.make_service("Personal training")                                    # the same coach
    assert book(w, w.people.priya, group, hour=10).status_code == 201

    clash = book(w, w.people.ravi, other, hour=10)
    assert clash.status_code == 409 and clash.json()["detail"]["code"] == "PROVIDER_UNAVAILABLE"
    day = day_of(3)
    seen = w.client.get(f"/api/v1/services/{other.id}/available-slots", params={"date": day.isoformat()}).json()["days"][0]["slots"]
    assert [s["reason"] for s in seen] == [None, "provider_busy", None]
    assert book(w, w.people.ravi, group, hour=10).status_code == 201                # same service, same start: the coach teaches both
    assert book(w, w.people.ravi, other, hour=11).status_code == 201                # a different hour is free


def test_the_same_person_cannot_book_the_same_slot_twice(w):
    service = w.make_service(max_participants=5)
    first = book(w, w.people.priya, service)

    again = book(w, w.people.priya, service)

    assert again.status_code == 409 and again.json()["detail"]["code"] == "DUPLICATE_BOOKING" and again.json()["detail"]["booking_id"] == first.json()["id"]


def test_the_idempotency_key_makes_a_repeat_return_the_first_booking(w):
    service = w.make_service(max_participants=5)

    first = book(w, w.people.priya, service, key="abc-1")
    repeat = book(w, w.people.priya, service, key="abc-1")

    assert first.status_code == 201 and repeat.status_code == 200 and repeat.json()["id"] == first.json()["id"]
    assert w.db.query(ServiceBooking).count() == 1
    assert book(w, w.people.ravi, service, key="abc-1").status_code == 201                  # keys belong to a person


# =============================================================================================================
# 3. paid services: the hold, payment status, approval
# =============================================================================================================

def test_a_paid_booking_holds_the_slot_until_it_is_paid(w):
    service = w.make_service(price=500, max_participants=1, provider=False)

    held = book(w, w.people.priya, service)

    b = held.json()
    assert held.status_code == 201 and b["status"] == "pending" and b["payment_status"] == "unpaid" and b["amount"] == 500 and b["currency"] == "INR" and b["hold_expires_at"]
    assert categories(w) == []                                                       # nothing to say until it is paid
    assert book(w, w.people.ravi, service).json()["detail"]["code"] == "SLOT_FULL"    # the hold keeps the place

    paid = w.as_user(w.people.priya).post(f"{API}/{b['id']}/pay")
    assert paid.status_code == 200 and paid.json()["status"] == "confirmed" and paid.json()["payment_status"] == "paid" and paid.json()["hold_expires_at"] is None
    assert [h["action"] for h in paid.json()["history"]] == ["created", "paid", "confirmed"]
    assert categories(w) == ["service_booking_confirmed", "service_booking_new"]              # the customer, then the clinic
    assert w.as_user(w.people.priya).post(f"{API}/{b['id']}/pay").json()["detail"]["code"] == "NOTHING_TO_PAY"


def test_an_unpaid_hold_that_runs_out_gives_the_slot_back(w):
    service = w.make_service(price=500, provider=False)
    b = book(w, w.people.priya, service).json()
    row = booking_row(w, b["id"])
    row.hold_expires_at = datetime.utcnow() - timedelta(minutes=1)
    w.db.commit()

    freed = book(w, w.people.ravi, service)

    assert freed.status_code == 201
    mine = w.as_user(w.people.priya).get(f"/api/v1/users/me/service-bookings").json()["items"][0]
    assert mine["status"] == "expired"
    late = w.as_user(w.people.priya).post(f"{API}/{b['id']}/pay")
    assert late.status_code == 409 and late.json()["detail"]["code"] == "HOLD_EXPIRED"


def test_paying_in_the_app_is_a_demo_switch_and_the_admin_can_always_record_it(w, monkeypatch):
    service = w.make_service(price=500, provider=False)
    b = book(w, w.people.priya, service).json()
    monkeypatch.setattr(settings, "SERVICE_BOOKING_DEMO_PAYMENT", False)

    refused = w.as_user(w.people.priya).post(f"{API}/{b['id']}/pay")
    recorded = w.as_user(w.people.admin).post(f"{API}/{b['id']}/payment", json={"reference": "UPI 4411"})

    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "PAYMENT_NOT_AVAILABLE"
    assert recorded.status_code == 200 and recorded.json()["payment_status"] == "paid" and recorded.json()["status"] == "confirmed"
    assert w.as_user(w.people.priya).post(f"{API}/{b['id']}/payment").status_code == 403
    assert w.as_user(w.people.other_admin).post(f"{API}/{b['id']}/payment").status_code == 404


def test_requires_approval_no_longer_holds_a_new_booking_back(w):
    service = w.make_service(requires_approval=True)            # the old setting is still accepted and stored

    b = book(w, w.people.priya, service).json()

    assert b["status"] == "confirmed" and b["stage"] == "confirmed" and b["payment_status"] == "not_required" and b["source"] == "customer"
    assert categories(w) == ["service_booking_confirmed", "service_booking_new"]
    staff_note = [n for n in w.sent if n["category"] == "service_booking_new"][0]
    assert {str(u) for u in staff_note["user_ids"]} == {w.people.coach["id"], w.people.admin["id"]}       # the provider and the admins


def test_a_paid_booking_is_confirmed_by_its_payment_even_with_the_old_approval_setting(w):
    service = w.make_service(price=200, requires_approval=True)
    b = book(w, w.people.priya, service).json()
    assert b["stage"] == "pending_payment"

    paid = w.as_user(w.people.priya).post(f"{API}/{b['id']}/pay").json()

    assert paid["status"] == "confirmed" and paid["payment_status"] == "paid" and categories(w) == ["service_booking_confirmed", "service_booking_new"]


def test_approval_bookings_from_before_are_listed_and_never_changed_automatically(w):
    service = w.make_service(max_participants=3)
    b = book(w, w.people.priya, service).json()
    row = booking_row(w, b["id"])
    row.status = "pending"                                       # what an approval booking made before the change looks like
    w.db.commit()
    admin = w.as_user(w.people.admin)

    listed = admin.get(f"/api/v1/enterprises/{w.ent.id}/service-bookings", params={"stage": "legacy_pending_approval"}).json()
    summary = admin.get(f"/api/v1/enterprises/{w.ent.id}/service-bookings/summary").json()
    from app.services import service_booking_service as svc
    svc.expire_stale_holds(w.db)

    assert [i["id"] for i in listed["items"]] == [b["id"]] and listed["items"][0]["stage"] == "legacy_pending_approval"
    assert summary["legacy_pending_approval"] == 1 and summary["pending_payment"] == 0
    assert booking_row(w, b["id"]).status == "pending"                                     # nothing touched it
    assert admin.patch(f"{API}/{b['id']}/status", json={"status": "confirmed"}).json()["status"] == "confirmed"     # a person decides it


def test_a_provider_cannot_mark_a_booking_paid_when_creating_it(w):
    service = w.make_service(price=300, max_participants=3)

    resp = w.as_user(w.people.coach).post(API, json={"service_id": str(service.id), "starts_at": slot_start(w, service), "customer_name": "Walk In", "paid": True})

    assert resp.status_code == 403 and resp.json()["detail"]["code"] == "PROVIDER_CANNOT_RECORD_PAYMENT"
    assert w.db.query(ServiceBooking).count() == 0                                                   # nothing was created
    ok = w.as_user(w.people.coach).post(API, json={"service_id": str(service.id), "starts_at": slot_start(w, service), "customer_name": "Walk In"})
    assert ok.status_code == 201 and ok.json()["payment_status"] == "unpaid" and ok.json()["status"] == "pending"      # still unpaid, held, for the Enterprise Admin to record
    assert w.as_user(w.people.coach).post(f"{API}/{ok.json()['id']}/payment").status_code == 403


def test_an_admin_can_book_for_a_customer_and_a_paid_one_is_confirmed(w):
    service = w.make_service(price=300, max_participants=3)

    resp = w.as_user(w.people.admin).post(API, json={"service_id": str(service.id), "starts_at": slot_start(w, service), "customer_name": "Walk In", "customer_email": "walk@example.test",
                                                      "paid": True})

    b = resp.json()
    assert resp.status_code == 201 and b["status"] == "confirmed" and b["payment_status"] == "paid" and b["customer"]["name"] == "Walk In" and b["customer"]["user_id"] is None
    assert b["created_by_role"] == "admin" and [h["action"] for h in b["history"]] == ["created", "paid"] and b["history"][0]["actor_name"] == "Clinic Admin"
    missing = w.as_user(w.people.admin).post(API, json={"service_id": str(service.id), "starts_at": slot_start(w, service, hour=11)})
    assert missing.status_code == 422 and missing.json()["detail"]["code"] == "CUSTOMER_REQUIRED"
    other = w.as_user(w.people.other_admin).post(API, json={"service_id": str(service.id), "starts_at": slot_start(w, service, hour=11), "customer_name": "X"})
    assert other.status_code == 403 and other.json()["detail"]["code"] == "NOT_YOUR_SERVICE"


# =============================================================================================================
# 4. who sees what
# =============================================================================================================

def test_a_booking_is_only_visible_to_its_customer_its_enterprise_its_provider_and_the_super_admin(w):
    service = w.make_service()
    b = book(w, w.people.priya, service).json()
    p = w.people

    assert w.as_user(p.priya).get(f"{API}/{b['id']}").status_code == 200
    assert w.as_user(p.admin).get(f"{API}/{b['id']}").status_code == 200
    assert w.as_user(p.coach).get(f"{API}/{b['id']}").status_code == 200
    assert w.as_user(p.root).get(f"{API}/{b['id']}").status_code == 200
    for stranger in (p.ravi, p.other_admin, p.other_coach):
        resp = w.as_user(stranger).get(f"{API}/{b['id']}")
        assert resp.status_code == 404 and resp.json()["detail"]["code"] == "BOOKING_NOT_FOUND", stranger["name"]
    assert w.as_user(p.ravi).get(f"{API}/{b['id']}/history").status_code == 404
    assert w.as_user(p.ravi).post(f"{API}/{b['id']}/cancel", json={}).status_code == 404


def test_my_bookings_lists_only_mine_with_filters_and_paging(w):
    service = w.make_service(max_participants=5)
    mine = [book(w, w.people.priya, service, hour=h, offset=o).json() for h, o in ((9, 3), (10, 3), (11, 4))]
    book(w, w.people.ravi, service, hour=9, offset=5)
    row = booking_row(w, mine[0]["id"])
    row.status, row.starts_at, row.ends_at = "completed", datetime.utcnow() - timedelta(days=2), datetime.utcnow() - timedelta(days=2) + timedelta(hours=1)
    w.db.commit()
    url = "/api/v1/users/me/service-bookings"

    everything = w.as_user(w.people.priya).get(url).json()
    assert everything["total"] == 3 and all(i["customer"]["user_id"] == w.people.priya["id"] for i in everything["items"])
    assert [i["id"] for i in w.as_user(w.people.priya).get(url, params={"when": "upcoming"}).json()["items"]] == [mine[1]["id"], mine[2]["id"]]      # soonest first
    assert [i["id"] for i in w.as_user(w.people.priya).get(url, params={"when": "past"}).json()["items"]] == [mine[0]["id"]]
    assert w.as_user(w.people.priya).get(url, params={"status": "completed,cancelled"}).json()["total"] == 1
    page = w.as_user(w.people.priya).get(url, params={"page_size": 2, "page": 2}).json()
    assert page["total"] == 3 and len(page["items"]) == 1 and page["page"] == 2
    assert w.as_user(w.people.ravi).get(url).json()["total"] == 1
    assert w.as_user(w.people.priya).get(url, params={"when": "never"}).status_code == 422


def test_the_enterprise_list_filters_and_stays_inside_the_enterprise(w):
    yoga = w.make_service("Yoga", max_participants=5)
    spa = w.make_service("Massage", price=400, provider=False)
    foreign = w.make_service("Foreign service", enterprise=w.other_ent, tenant_id=w.other_tenant, provider=False)
    a = book(w, w.people.priya, yoga, hour=9).json()
    book(w, w.people.ravi, yoga, hour=9)
    book(w, w.people.ravi, spa, hour=10)
    book(w, w.people.priya, foreign, hour=10)
    url = f"/api/v1/enterprises/{w.ent.id}/service-bookings"
    admin = w.as_user(w.people.admin)

    body = admin.get(url).json()
    assert body["total"] == 3 and {i["service_name"] for i in body["items"]} == {"Yoga", "Massage"}
    assert admin.get(url, params={"q": "massage"}).json()["total"] == 1 and admin.get(url, params={"q": "ravi"}).json()["total"] == 2
    assert admin.get(url, params={"payment_status": "unpaid"}).json()["total"] == 1 and admin.get(url, params={"service_id": str(yoga.id)}).json()["total"] == 2
    assert admin.get(url, params={"status": "pending"}).json()["total"] == 1 and admin.get(url, params={"provider_id": w.people.coach["id"]}).json()["total"] == 2
    assert admin.get(url, params={"when": "past"}).json()["total"] == 0 and admin.get(url, params={"page_size": 2}).json()["items"].__len__() == 2
    day = day_of(3).isoformat()
    assert admin.get(url, params={"date_from": day, "date_to": day}).json()["total"] == 3
    first = admin.get(url, params={"q": "priya"}).json()["items"][0]
    assert first["id"] == a["id"] and first["customer"]["name"] == "Priya Sharma" and first["customer"]["email"] == "priya.sharma@example.test"

    assert w.as_user(w.people.other_admin).get(url).status_code == 404                       # another enterprise's id
    assert w.as_user(w.people.priya).get(url).status_code == 404                              # a customer is not staff
    assert w.as_user(w.people.coach).get(url).json()["total"] == 2                           # the provider: their own bookings
    assert w.as_user(w.people.other_coach).get(url).json()["total"] == 0
    assert w.as_user(w.people.root).get(url).json()["total"] == 3


# =============================================================================================================
# 5. cancelling
# =============================================================================================================

def test_a_customer_cancels_at_once_inside_the_free_window_and_a_paid_booking_records_a_full_refund(w):
    service = w.make_service(price=500)
    b = book(w, w.people.priya, service).json()
    w.as_user(w.people.priya).post(f"{API}/{b['id']}/pay")
    w.sent.clear()

    resp = w.as_user(w.people.priya).post(f"{API}/{b['id']}/cancel", json={"reason": "Changed my mind"})

    d = resp.json()
    assert resp.status_code == 200 and d["status"] == "cancelled" and d["payment_status"] == "paid" and d["refund_status"] == "pending" and d["can_cancel"] is False
    assert d["cancellation"]["refund_percent"] == 100 and d["cancellation"]["refund_amount"] == 500 and d["cancellation"]["cancelled_by_role"] == "customer"
    assert d["cancellation"]["reason"] == "Changed my mind" and d["cancel_blocked_reason"] == "This booking is cancelled"
    assert [h["action"] for h in d["history"]][-1] == "cancelled"
    assert categories(w) == ["service_booking_cancelled", "service_booking_cancelled"]            # the customer, and the staff
    assert "reason" in w.sent[0]["metadata"]                                                      # in the feed; the push pipeline leaves it out
    assert book(w, w.people.ravi, service).status_code == 201                                     # the slot is free again


def test_cancelling_an_unpaid_pending_booking_is_instant_and_needs_no_refund(w):
    service = w.make_service(price=500)
    b = book(w, w.people.priya, service).json()

    d = w.as_user(w.people.priya).post(f"{API}/{b['id']}/cancel", json={}).json()

    assert d["status"] == "cancelled" and d["payment_status"] == "unpaid" and d["cancellation"]["refund_amount"] == 0


def test_inside_the_window_a_cancellation_is_a_request_the_admin_decides(w):
    service = w.make_service(price=400, cancellation_rules={"free_cancellation_hours": 24, "refund_percent_before": 100, "refund_percent_after": 50, "allow_request_after_window": True})
    b = book(w, w.people.priya, service).json()
    w.as_user(w.people.priya).post(f"{API}/{b['id']}/pay")
    row = booking_row(w, b["id"])
    row.starts_at, row.ends_at = datetime.utcnow() + timedelta(hours=5), datetime.utcnow() + timedelta(hours=6)       # 5 hours away: inside the 24 h window
    w.db.commit()
    detail = w.as_user(w.people.priya).get(f"{API}/{b['id']}").json()
    assert detail["can_cancel"] is True and detail["cancel_mode"] == "request"
    w.sent.clear()

    too_short = w.as_user(w.people.priya).post(f"{API}/{b['id']}/cancel", json={"reason": "no"})
    asked = w.as_user(w.people.priya).post(f"{API}/{b['id']}/cancel", json={"reason": "I am unwell"})

    assert too_short.status_code == 422 and too_short.json()["detail"]["code"] == "REASON_REQUIRED"
    assert asked.status_code == 200 and asked.json()["status"] == "cancellation_requested" and asked.json()["can_cancel"] is False
    assert asked.json()["cancel_blocked_reason"] == "A cancellation request is already waiting for the clinic" and categories(w) == ["service_booking_cancellation_requested"]
    listed = w.as_user(w.people.admin).get(f"/api/v1/enterprises/{w.ent.id}/service-bookings/cancellation-requests").json()
    assert [i["id"] for i in listed["items"]] == [b["id"]]
    assert w.as_user(w.people.admin).patch(f"{API}/{b['id']}/status", json={"status": "cancelled", "reason": "x" * 5}).json()["detail"]["code"] == "DECIDE_THE_REQUEST"

    approved = w.as_user(w.people.admin).post(f"{API}/{b['id']}/cancellation/approve", json={})
    d = approved.json()
    assert approved.status_code == 200 and d["status"] == "cancelled" and d["payment_status"] == "paid" and d["refund_status"] == "pending"
    assert d["cancellation"]["refund_percent"] == 50 and d["cancellation"]["refund_amount"] == 200 and d["cancellation"]["cancelled_by_role"] == "admin"
    assert categories(w)[-1] == "service_booking_cancelled"
    assert w.as_user(w.people.admin).get(f"/api/v1/enterprises/{w.ent.id}/service-bookings/cancellation-requests").json()["items"] == []
    assert w.as_user(w.people.admin).post(f"{API}/{b['id']}/cancellation/approve", json={}).json()["detail"]["code"] == "NO_REQUEST"


def test_a_rejected_request_restores_the_booking_and_needs_a_reason(w):
    service = w.make_service()
    b = book(w, w.people.priya, service).json()
    row = booking_row(w, b["id"])
    row.starts_at, row.ends_at = datetime.utcnow() + timedelta(hours=3), datetime.utcnow() + timedelta(hours=4)
    w.db.commit()
    w.as_user(w.people.priya).post(f"{API}/{b['id']}/cancel", json={"reason": "Not feeling well"})
    w.sent.clear()

    no_reason = w.as_user(w.people.admin).post(f"{API}/{b['id']}/cancellation/reject", json={})
    rejected = w.as_user(w.people.admin).post(f"{API}/{b['id']}/cancellation/reject", json={"reason": "Too late to free the slot"})

    assert no_reason.status_code == 422 and no_reason.json()["detail"]["code"] == "REASON_REQUIRED"
    assert rejected.status_code == 200 and rejected.json()["status"] == "confirmed" and rejected.json()["cancellation"]["decision_reason"] == "Too late to free the slot"
    assert categories(w) == ["service_booking_cancellation_rejected"] and w.sent[0]["metadata"]["reason"] == "Too late to free the slot"
    assert w.as_user(w.people.coach).post(f"{API}/{b['id']}/cancellation/approve", json={}).status_code == 403
    assert [h["action"] for h in rejected.json()["history"]][-2:] == ["cancellation_requested", "cancellation_rejected"]


def test_cancelling_can_be_blocked_by_the_policy_or_because_it_started(w):
    strict = w.make_service("Strict", cancellation_rules={"free_cancellation_hours": 48, "allow_request_after_window": False})
    b = book(w, w.people.priya, strict).json()
    row = booking_row(w, b["id"])
    row.starts_at, row.ends_at = datetime.utcnow() + timedelta(hours=5), datetime.utcnow() + timedelta(hours=6)
    w.db.commit()

    blocked = w.as_user(w.people.priya).post(f"{API}/{b['id']}/cancel", json={"reason": "please"})

    d = blocked.json()["detail"]
    assert blocked.status_code == 409 and d["code"] == "CANCEL_NOT_ALLOWED" and "48 hours" in d["message"] and "Free cancellation until 48 hours" in d["policy"]
    detail = w.as_user(w.people.priya).get(f"{API}/{b['id']}").json()
    assert detail["can_cancel"] is False and detail["cancel_blocked_reason"] == d["message"]
    row = booking_row(w, b["id"])
    row.starts_at, row.ends_at = datetime.utcnow() - timedelta(minutes=5), datetime.utcnow() + timedelta(minutes=55)
    w.db.commit()
    started = w.as_user(w.people.priya).post(f"{API}/{b['id']}/cancel", json={"reason": "please"})
    assert started.status_code == 409 and started.json()["detail"]["message"] == "This booking has already started"


# =============================================================================================================
# 6. status changes by staff
# =============================================================================================================

def test_staff_move_a_booking_through_its_statuses(w):
    service = w.make_service()
    b = book(w, w.people.priya, service).json()
    assert b["status"] == "confirmed"
    admin = w.as_user(w.people.admin)

    early = admin.patch(f"{API}/{b['id']}/status", json={"status": "in_progress"})
    assert early.status_code == 409 and early.json()["detail"]["code"] == "NOT_STARTED"
    assert admin.patch(f"{API}/{b['id']}/status", json={"status": "completed"}).json()["detail"]["code"] == "NOT_STARTED"
    invalid = admin.patch(f"{API}/{b['id']}/status", json={"status": "confirmed"}).json()["detail"]
    assert invalid["code"] == "INVALID_TRANSITION" and invalid["allowed"] == ["in_progress", "completed", "no_show", "cancelled"]
    detail = admin.get(f"{API}/{b['id']}").json()
    assert detail["allowed_statuses"] == ["in_progress", "completed", "no_show", "cancelled"]

    row = booking_row(w, b["id"])
    row.starts_at, row.ends_at = datetime.utcnow() - timedelta(minutes=10), datetime.utcnow() + timedelta(minutes=50)
    w.db.commit()
    w.sent.clear()
    started = admin.patch(f"{API}/{b['id']}/status", json={"status": "in_progress"}).json()
    done = admin.patch(f"{API}/{b['id']}/status", json={"status": "completed"}).json()
    assert started["status"] == "in_progress" and done["status"] == "completed" and done["can_cancel"] is False
    assert [h["action"] for h in done["history"]][-2:] == ["in_progress", "completed"] and categories(w) == ["service_booking_completed"]
    assert admin.patch(f"{API}/{b['id']}/status", json={"status": "cancelled", "reason": "too late"}).json()["detail"]["code"] == "INVALID_TRANSITION"


def test_no_show_and_the_cancel_reason_rules(w):
    service = w.make_service(max_participants=5)
    a = book(w, w.people.priya, service, hour=9).json()
    c = book(w, w.people.ravi, service, hour=10).json()
    row = booking_row(w, a["id"])
    row.starts_at, row.ends_at = datetime.utcnow() - timedelta(minutes=30), datetime.utcnow() + timedelta(minutes=30)
    w.db.commit()
    admin = w.as_user(w.people.admin)

    missed = admin.patch(f"{API}/{a['id']}/status", json={"status": "no_show"}).json()
    assert missed["status"] == "no_show" and "service_booking_no_show" in categories(w)
    assert w.as_user(w.people.priya).get(f"{API}/{a['id']}").json()["status"] == "no_show"                       # the customer sees it
    admin = w.as_user(w.people.admin)                                                                           # (the test client is shared: back to the admin)
    no_reason = admin.patch(f"{API}/{c['id']}/status", json={"status": "cancelled"})
    assert no_reason.status_code == 422 and no_reason.json()["detail"]["code"] == "REASON_REQUIRED"
    cancelled = admin.patch(f"{API}/{c['id']}/status", json={"status": "cancelled", "reason": "Clinic closed that day"}).json()
    assert cancelled["status"] == "cancelled" and cancelled["cancellation"]["cancelled_by_role"] == "admin"
    assert w.as_user(w.people.ravi).patch(f"{API}/{c['id']}/status", json={"status": "completed"}).status_code == 403
    assert admin.patch(f"{API}/{c['id']}/status", json={"status": "bogus"}).status_code == 422


def test_a_paid_booking_cannot_be_confirmed_before_it_is_paid(w):
    service = w.make_service(price=300)
    b = book(w, w.people.priya, service).json()

    refused = w.as_user(w.people.admin).patch(f"{API}/{b['id']}/status", json={"status": "confirmed"})
    paid = w.as_user(w.people.admin).post(f"{API}/{b['id']}/payment", json={})

    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "PAYMENT_REQUIRED" and paid.json()["status"] == "confirmed"


def test_a_provider_can_only_run_their_own_bookings(w):
    service = w.make_service()
    b = book(w, w.people.priya, service).json()
    row = booking_row(w, b["id"])
    row.starts_at, row.ends_at = datetime.utcnow() - timedelta(minutes=10), datetime.utcnow() + timedelta(minutes=50)
    w.db.commit()

    assert w.as_user(w.people.other_coach).patch(f"{API}/{b['id']}/status", json={"status": "in_progress"}).status_code == 404
    assert w.as_user(w.people.coach).patch(f"{API}/{b['id']}/status", json={"status": "cancelled", "reason": "no"}).json()["detail"]["code"] == "INVALID_TRANSITION"
    started = w.as_user(w.people.coach).patch(f"{API}/{b['id']}/status", json={"status": "in_progress"}).json()
    assert started["status"] == "in_progress" and started["allowed_statuses"] == ["completed"]
    assert w.as_user(w.people.coach).post(f"{API}/{b['id']}/cancel", json={"reason": "can't"}).status_code == 403


# =============================================================================================================
# 7. venue, meeting, history, settings
# =============================================================================================================

def test_the_meeting_link_opens_shortly_before_the_start_and_staff_always_see_it(w):
    service = w.make_service("Online consult", delivery_modes=["virtual"], meeting_url="https://meet.example/xyz", meeting_provider="zoom", meeting_passcode="4455",
                             meeting_instructions="Join from a quiet room")
    b = book(w, w.people.priya, service).json()
    assert b["venue"] is None and b["delivery_mode"] == "virtual"

    early = w.as_user(w.people.priya).get(f"{API}/{b['id']}").json()
    assert early["can_join"] is False and early["meeting"]["url"] is None and early["meeting"]["passcode"] is None and early["meeting"]["available"] is False
    assert early["meeting"]["provider"] == "zoom" and early["meeting"]["joins_at"] == (datetime.fromisoformat(early["starts_at"]) - timedelta(minutes=10)).isoformat()
    assert early["join_blocked_reason"] == "The meeting link opens 10 minutes before the start"
    assert w.as_user(w.people.admin).get(f"{API}/{b['id']}").json()["meeting"]["url"] == "https://meet.example/xyz"

    row = booking_row(w, b["id"])
    row.starts_at, row.ends_at = datetime.utcnow() + timedelta(minutes=5), datetime.utcnow() + timedelta(minutes=65)
    w.db.commit()
    now_open = w.as_user(w.people.priya).get(f"{API}/{b['id']}").json()
    assert now_open["can_join"] is True and now_open["join_blocked_reason"] is None
    assert now_open["meeting"] == {"provider": "zoom", "joins_at": now_open["meeting"]["joins_at"], "available": True, "url": "https://meet.example/xyz", "passcode": "4455",
                                   "instructions": "Join from a quiet room"}
    row = booking_row(w, b["id"])
    row.starts_at, row.ends_at = datetime.utcnow() - timedelta(hours=3), datetime.utcnow() - timedelta(hours=2)
    w.db.commit()
    assert w.as_user(w.people.priya).get(f"{API}/{b['id']}").json()["meeting"]["url"] is None                      # ended


def test_a_hybrid_booking_has_both_the_venue_and_the_meeting(w):
    service = w.make_service("Hybrid class", delivery_modes=["hybrid"], meeting_url="https://meet.example/h")

    b = book(w, w.people.priya, service).json()

    assert b["delivery_mode"] == "hybrid" and b["venue"]["city"] == "Hyderabad" and b["meeting"]["provider"] is None and b["meeting"]["url"] is None


def test_the_delivery_mode_is_worked_out_from_the_old_text_field(w):
    assert slot_rules.delivery_modes_of(SimpleNamespace(delivery_modes=None, delivery_format="Online (Zoom)")) == ["virtual"]
    assert slot_rules.delivery_modes_of(SimpleNamespace(delivery_modes=None, delivery_format="Hybrid")) == ["hybrid"]
    assert slot_rules.delivery_modes_of(SimpleNamespace(delivery_modes=None, delivery_format=None)) == ["in_person"]


def test_booking_settings_are_for_staff_and_include_the_meeting_link(w):
    service = w.make_service(meeting_url="https://meet.example/s", meeting_passcode="1", requires_approval=True)
    url = f"/api/v1/services/{service.id}/booking-settings"

    body = w.as_user(w.people.admin).get(url).json()

    assert body["meeting_url"] == "https://meet.example/s" and body["requires_approval"] is True and body["time_zone"] == "Asia/Kolkata" and body["max_participants"] is None
    assert body["cancellation_rules"] == {"free_cancellation_hours": 24, "refund_percent_before": 100, "refund_percent_after": 0, "allow_request_after_window": True}
    assert w.as_user(w.people.priya).get(url).status_code == 403
    assert w.as_user(w.people.other_admin).get(url).status_code in (403, 404)


def test_the_history_endpoint_lists_every_change(w):
    service = w.make_service(price=100)
    b = book(w, w.people.priya, service).json()
    w.as_user(w.people.priya).post(f"{API}/{b['id']}/pay")
    w.as_user(w.people.priya).post(f"{API}/{b['id']}/cancel", json={"reason": "plans changed"})

    own = w.as_user(w.people.priya).get(f"{API}/{b['id']}/history").json()
    staff = w.as_user(w.people.admin).get(f"{API}/{b['id']}/history").json()

    assert [h["action"] for h in own] == ["created", "paid", "confirmed", "cancelled"] and own[-1]["reason"] == "plans changed" and own[-1]["from_status"] == "confirmed"
    assert all(h["actor_name"] is None for h in own) and staff[-1]["actor_name"] == "Priya Sharma" and staff[-1]["actor_role"] == "customer"


# =============================================================================================================
# 8. reminders and holds
# =============================================================================================================

def test_the_reminder_steps():
    hours = [24, 1]
    assert notify.due_step(timedelta(hours=30), hours) is None and notify.due_step(timedelta(hours=23), hours) == 24
    assert notify.due_step(timedelta(minutes=40), hours) == 1 and notify.due_step(timedelta(minutes=-1), hours) is None


def test_a_reminder_goes_out_once_per_step_and_only_for_confirmed_bookings(w):
    service = w.make_service(max_participants=5)
    b = book(w, w.people.priya, service, hour=9).json()
    pending = book(w, w.people.ravi, w.make_service("Paid", price=100, provider=False), hour=9).json()
    row = booking_row(w, b["id"])
    row.starts_at, row.ends_at = datetime.utcnow() + timedelta(hours=20), datetime.utcnow() + timedelta(hours=21)
    w.db.commit()
    w.sent.clear()

    first = notify.send_due_reminders(w.db)
    again = notify.send_due_reminders(w.db)

    assert first == 1 and again == 0 and pending["status"] == "pending"
    [note] = w.sent
    assert note["category"] == "service_booking_reminder" and note["metadata"]["kind"] == "24h" and note["metadata"]["booking_id"] == b["id"]
    assert note["metadata"]["entity_type"] == "service_booking" and all(isinstance(v, str) for v in note["metadata"].values())
    row = booking_row(w, b["id"])
    row.starts_at, row.ends_at = datetime.utcnow() + timedelta(minutes=40), datetime.utcnow() + timedelta(minutes=100)
    w.db.commit()
    assert notify.send_due_reminders(w.db) == 1 and w.sent[-1]["metadata"]["kind"] == "1h"


def test_expired_holds_are_swept(w):
    from app.services import service_booking_service as svc

    service = w.make_service(price=100, provider=False)
    b = book(w, w.people.priya, service).json()
    row = booking_row(w, b["id"])
    row.hold_expires_at = datetime.utcnow() - timedelta(seconds=1)
    w.db.commit()

    assert svc.expire_stale_holds(w.db) == 1 and booking_row(w, b["id"]).status == "expired"
    assert [e.action for e in booking_row(w, b["id"]).history][-1] == "expired" and booking_row(w, b["id"]).history[-1].actor_role == "system"


# =============================================================================================================
# 9. telling the staff: the identity lookup of the admins gets the caller's bearer token
# =============================================================================================================

def test_the_staff_lookup_gets_the_callers_token_when_a_booking_needs_approval_or_is_cancelled(w, monkeypatch):
    lookups = []

    def admins(_db, item, access_token=None):
        lookups.append(access_token)
        return ([__import__("uuid").UUID(w.people.admin["id"])] if access_token else []), item.tenant_id        # the identity service answers only with a bearer token

    monkeypatch.setattr("app.services.event_notification_service.resolve_enterprise_admin_user_ids", admins)
    service = w.make_service(requires_approval=True)
    headers = {"Authorization": "Bearer customer-token"}

    made = w.as_user(w.people.priya).post(API, json={"service_id": str(service.id), "starts_at": slot_start(w, service)}, headers=headers)
    assert made.status_code == 201 and lookups == ["customer-token"]
    [note] = [n for n in w.sent if n["category"] == "service_booking_new"]
    assert {str(u) for u in note["user_ids"]} == {w.people.coach["id"], w.people.admin["id"]}                  # the provider and the admin

    w.sent.clear()
    cancelled = w.as_user(w.people.priya).post(f"{API}/{made.json()['id']}/cancel", json={}, headers=headers)
    assert cancelled.status_code == 200 and lookups == ["customer-token", "customer-token"]
    assert {str(u) for n in w.sent if n["category"] == "service_booking_cancelled" for u in n["user_ids"]} == {w.people.priya["id"], w.people.coach["id"], w.people.admin["id"]}


def test_without_a_token_the_admins_are_not_found_and_that_is_logged(w, monkeypatch, caplog):
    monkeypatch.setattr("app.services.event_notification_service.resolve_enterprise_admin_user_ids", lambda _db, item, access_token=None: ([], item.tenant_id))
    service = w.make_service(requires_approval=True)

    with caplog.at_level("WARNING"):
        made = book(w, w.people.priya, service)

    assert made.status_code == 201                                                       # the customer's booking is never failed by this
    assert any("no Enterprise Admin found to notify" in r.getMessage() for r in caplog.records)
    assert [str(u) for n in w.sent if n["category"] == "service_booking_new" for u in n["user_ids"]] == [w.people.coach["id"]]
