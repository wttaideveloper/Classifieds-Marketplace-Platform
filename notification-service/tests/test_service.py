from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app import pipeline
from app.config import get_settings
from app.db import Base, get_db, make_engine
from app.main import app
from app.models import Delivery, NudgeJob
from app.providers import LogProviders, set_providers

PRODUCER = {"X-API-Key": "billing-key"}
ADMIN = {"X-API-Key": "admin-key"}


@pytest.fixture
def svc(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "PRODUCER_KEYS", "billing:billing-key,chat:chat-key")
    monkeypatch.setattr(settings, "ADMIN_KEY", "admin-key")
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = Session()
    providers = LogProviders()
    set_providers(providers)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    yield type("S", (), {"client": client, "db": db, "providers": providers})
    app.dependency_overrides.clear()
    set_providers(None)
    db.close()


def register(svc, event="payment.succeeded", **over):
    body = {"event": event, "category": "payments", "title": "Payment of {currency} {amount} received", "body": "Thanks, {user_id}.",
            "channels": ["in_app", "push", "email"], **over}
    resp = svc.client.put(f"/v1/admin/event-types/{event}", json=body, headers=ADMIN)
    assert resp.status_code == 200, resp.text
    return resp.json()


def send(svc, event="payment.succeeded", user="u1", **over):
    body = {"event": event, "recipients": [{"user_id": user, "email": f"{user}@example.com"}], "data": {"amount": "1180.00", "currency": "INR"}, **over}
    return svc.client.post("/v1/events", json=body, headers=PRODUCER)


def device(svc, user="u1", token="tok-abcdefghij"):
    assert svc.client.post(f"/v1/users/{user}/devices", json={"token": token}, headers=PRODUCER).status_code == 201


def test_an_event_becomes_a_rendered_notification_and_deliveries(svc):
    register(svc)
    device(svc)
    resp = send(svc, idempotency_key="pay-1")
    assert resp.status_code == 202
    [result] = resp.json()["results"]
    assert result["status"] == "created" and result["channels"] == ["in_app", "push", "email"]

    inbox = svc.client.get("/v1/users/u1/notifications", headers=PRODUCER).json()
    assert inbox["unread_count"] == 1 and inbox["pagination"]["total"] == 1
    item = inbox["items"][0]
    assert item["title"] == "Payment of INR 1180.00 received" and item["body"] == "Thanks, u1." and item["category"] == "payments"

    assert pipeline.deliver(svc.db, svc.providers) == 2
    sent = {m["channel"]: m for m in svc.providers.sent}
    assert sent["push"]["tokens"] == ["tok-abcdefghij"] and sent["push"]["data"]["event"] == "payment.succeeded"
    assert sent["email"]["to"] == "u1@example.com" and sent["email"]["subject"] == "Payment of INR 1180.00 received"
    assert {d["status"] for d in svc.client.get("/v1/admin/deliveries", headers=ADMIN).json()} == {"sent"}


def test_the_same_idempotency_key_is_sent_once_per_person(svc):
    register(svc)
    assert send(svc, idempotency_key="k1").json()["results"][0]["status"] == "created"
    again = send(svc, idempotency_key="k1").json()["results"][0]
    assert again["status"] == "duplicate" and again["notification_id"]
    assert send(svc, idempotency_key="k1", user="u2").json()["results"][0]["status"] == "created"      # another person
    assert svc.client.get("/v1/users/u1/notifications", headers=PRODUCER).json()["pagination"]["total"] == 1


def test_unknown_events_and_wrong_keys_are_refused(svc):
    assert send(svc, event="nope").status_code == 422
    assert send(svc, event="nope", title="One-off", body="Hello").status_code == 202                      # an ad-hoc event with its own wording
    assert svc.client.post("/v1/events", json={"event": "x", "recipients": [{"user_id": "u"}]}).status_code == 401
    assert svc.client.post("/v1/events", json={"event": "x", "recipients": [{"user_id": "u"}]}, headers={"X-API-Key": "bad"}).status_code == 401
    assert svc.client.put("/v1/admin/event-types/a", json={"event": "a", "title": "A"}, headers=PRODUCER).status_code == 403
    assert svc.client.get("/v1/admin/deliveries", headers={}).status_code == 401
    assert svc.client.put("/v1/admin/event-types/a", json={"event": "b", "title": "A"}, headers=ADMIN).status_code == 422


def test_preferences_switch_off_push_and_email_but_not_critical_events_or_the_inbox(svc):
    register(svc)
    register(svc, "security.alert", category="security", priority="critical", title="New sign-in")
    device(svc)
    assert svc.client.put("/v1/users/u1/preferences", json={"category": "payments", "channel": "push", "enabled": False}, headers=PRODUCER).status_code == 200
    svc.client.put("/v1/users/u1/preferences", json={"category": "security", "channel": "push", "enabled": False}, headers=PRODUCER)

    assert send(svc).json()["results"][0]["channels"] == ["in_app", "email"]                                # push is off for payments
    assert send(svc, event="security.alert").json()["results"][0]["channels"] == ["in_app", "push", "email"]   # critical ignores preferences
    assert svc.client.get("/v1/users/u1/preferences", headers=PRODUCER).json()["items"]
    assert svc.client.get("/v1/users/u1/notifications", headers=PRODUCER).json()["pagination"]["total"] == 2


def test_failed_deliveries_are_retried_with_a_wait_then_given_up(svc, monkeypatch):
    monkeypatch.setattr(get_settings(), "MAX_ATTEMPTS", 3)
    register(svc, channels=["in_app", "email"])

    def broken(to, subject, body):
        raise RuntimeError("smtp down")

    svc.providers.email = broken
    send(svc)
    now = datetime.utcnow()
    assert pipeline.deliver(svc.db, svc.providers, now) == 1
    delivery = svc.db.query(Delivery).one()
    assert delivery.status == "pending" and delivery.attempts == 1 and "smtp down" in delivery.last_error
    assert pipeline.deliver(svc.db, svc.providers, now) == 0                                                # not due yet
    assert pipeline.deliver(svc.db, svc.providers, now + timedelta(seconds=31)) == 1
    pipeline.deliver(svc.db, svc.providers, now + timedelta(minutes=10))
    assert svc.db.query(Delivery).one().status == "failed" and svc.db.query(Delivery).one().attempts == 3


def test_a_person_with_no_device_is_skipped_and_a_dead_token_is_removed(svc):
    register(svc, channels=["in_app", "push"])
    send(svc)
    pipeline.deliver(svc.db, svc.providers)
    assert svc.db.query(Delivery).one().status == "skipped"

    device(svc, token="dead-token-1234")
    svc.providers.push = lambda tokens, title, body, data: ["dead-token-1234"]
    send(svc, idempotency_key="again")
    pipeline.deliver(svc.db, svc.providers)
    from app.models import Device
    assert svc.db.query(Device).count() == 0


def test_inbox_reading_marking_read_and_paging(svc):
    register(svc)
    for i in range(3):
        send(svc, idempotency_key=f"k{i}")
    page = svc.client.get("/v1/users/u1/notifications", params={"page_size": 2}, headers=PRODUCER).json()
    assert len(page["items"]) == 2 and page["pagination"] == {"total": 3, "page": 1, "page_size": 2, "total_pages": 2}

    first = page["items"][0]["id"]
    assert svc.client.post("/v1/users/u1/notifications/read", json={"ids": [first]}, headers=PRODUCER).json() == {"marked": 1}
    assert svc.client.post("/v1/users/u1/notifications/read", json={"ids": [first]}, headers=PRODUCER).json() == {"marked": 0}
    assert svc.client.get("/v1/users/u1/notifications/unread-count", headers=PRODUCER).json() == {"unread_count": 2}
    assert svc.client.post("/v1/users/u1/notifications/read", json={}, headers=PRODUCER).status_code == 422
    assert svc.client.post("/v1/users/u1/notifications/read", json={"all": True}, headers=PRODUCER).json() == {"marked": 2}
    assert svc.client.get("/v1/users/u1/notifications", params={"unread_only": True}, headers=PRODUCER).json()["items"] == []
    assert svc.client.get("/v1/users/u2/notifications", headers=PRODUCER).json()["items"] == []            # nobody else's inbox


def rule(svc, **over):
    register(svc, "order.created", category="orders", title="Order placed", channels=["in_app"])
    register(svc, "order.payment_reminder", category="orders", title="Order {order_id} is waiting for payment", channels=["in_app", "push"])
    body = {"name": "unpaid", "trigger_event": "order.created", "cancel_event": "payment.succeeded", "nudge_event": "order.payment_reminder",
            "delay_minutes": 60, "subject_field": "order_id", **over}
    resp = svc.client.post("/v1/admin/nudge-rules", json=body, headers=ADMIN)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_a_nudge_fires_when_the_follow_up_does_not_come(svc):
    rule(svc)
    send(svc, "order.created", data={"order_id": "o-1"})
    job = svc.db.query(NudgeJob).one()
    assert job.status == "pending" and job.subject == "o-1"
    assert pipeline.run_nudges(svc.db, datetime.utcnow() + timedelta(minutes=30)) == 0                       # too early
    assert pipeline.run_nudges(svc.db, datetime.utcnow() + timedelta(minutes=61)) == 1
    titles = [n["title"] for n in svc.client.get("/v1/users/u1/notifications", headers=PRODUCER).json()["items"]]
    assert "Order o-1 is waiting for payment" in titles
    assert svc.db.query(NudgeJob).one().status == "done"
    assert pipeline.run_nudges(svc.db, datetime.utcnow() + timedelta(days=2)) == 0                           # once only


def test_the_follow_up_cancels_the_nudge_only_for_the_same_person_and_subject(svc):
    rule(svc)
    register(svc)
    send(svc, "order.created", data={"order_id": "o-1"})
    send(svc, "order.created", data={"order_id": "o-2"})
    send(svc, "payment.succeeded", data={"order_id": "o-1"})
    send(svc, "payment.succeeded", user="u2", data={"order_id": "o-2"})                                       # someone else paid
    statuses = {j.subject: j.status for j in svc.db.query(NudgeJob).all()}
    assert statuses == {"o-1": "cancelled", "o-2": "pending"}
    assert pipeline.run_nudges(svc.db, datetime.utcnow() + timedelta(hours=2)) == 1


def test_a_nudge_can_repeat_and_a_repeated_trigger_does_not_double_it(svc):
    rule(svc, max_nudges=2, repeat_minutes=120)
    send(svc, "order.created", data={"order_id": "o-1"}, idempotency_key="a")
    send(svc, "order.created", data={"order_id": "o-1"}, idempotency_key="b")
    assert svc.db.query(NudgeJob).count() == 1
    t = datetime.utcnow()
    assert pipeline.run_nudges(svc.db, t + timedelta(minutes=61)) == 1
    assert svc.db.query(NudgeJob).one().status == "pending"
    assert pipeline.run_nudges(svc.db, t + timedelta(minutes=100)) == 0
    assert pipeline.run_nudges(svc.db, t + timedelta(minutes=190)) == 1
    assert svc.db.query(NudgeJob).one().status == "done"
    assert svc.client.get("/v1/users/u1/notifications", params={"category": "orders"}, headers=PRODUCER).json()["pagination"]["total"] == 4      # two order.created notices and two reminders


def test_nudge_rules_are_checked(svc):
    register(svc, "a.start", title="x")
    base = {"name": "r", "trigger_event": "a.start", "nudge_event": "a.start", "delay_minutes": 5}
    assert svc.client.post("/v1/admin/nudge-rules", json=base, headers=ADMIN).status_code == 422             # nudge = trigger
    assert svc.client.post("/v1/admin/nudge-rules", json={**base, "nudge_event": "missing.event"}, headers=ADMIN).status_code == 422
    register(svc, "a.nudge", title="y")
    ok = svc.client.post("/v1/admin/nudge-rules", json={**base, "nudge_event": "a.nudge"}, headers=ADMIN)
    assert ok.status_code == 201
    assert svc.client.post("/v1/admin/nudge-rules", json={**base, "nudge_event": "a.nudge"}, headers=ADMIN).status_code == 409
    off = svc.client.patch(f"/v1/admin/nudge-rules/{ok.json()['id']}/active", params={"active": False}, headers=ADMIN)
    assert off.status_code == 200 and off.json()["active"] is False
    send(svc, "a.start", data={"subject_id": "s"})
    assert svc.db.query(NudgeJob).count() == 0                                                                # a switched-off rule starts nothing


def test_health_and_template_safety(svc):
    assert svc.client.get("/health").json() == {"status": "ok"}
    assert pipeline.render("Hi {name}, {missing} {", {"name": "Asha"}) == "Hi {name}, {missing} {"
    assert pipeline.render("Hi {name}, {missing}", {"name": "Asha"}) == "Hi Asha, {missing}"
