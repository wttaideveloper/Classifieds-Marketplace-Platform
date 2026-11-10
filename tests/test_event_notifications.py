"""Event push notifications for the mobile app: the categories, the flat all-string `data`, who is told, and that nobody is told twice.

The platform pipeline (create_automatic_notification) is captured, so each test sees exactly what would reach the in-app feed and FCM; one test runs
the real pipeline and captures what is handed to FCM. Nothing is sent to Firebase.
"""
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.chat_model import DeviceToken, NotificationPreference
from app.models.event_aux_models import EventFeedback, EventRegistration, EventWaitlist
from app.models.notification_model import Notification, NotificationEventLog, NotificationLog, UserNotification
from app.schemas.event_schema import EventRegistrationCreate
from app.services import event_notifications as push
from app.services import notification_triggers as triggers
from event_sql_support import make_enterprise, make_event, make_registration, make_session

KEYS = {"category", "event_id", "event_name", "entity_type", "entity_id"}


@pytest.fixture
def env(monkeypatch):
    db = make_session()
    bind = db.get_bind()
    for model in (Notification, UserNotification, NotificationLog, NotificationEventLog, DeviceToken, NotificationPreference):
        model.__table__.create(bind=bind, checkfirst=True)
    tenant = uuid4()
    event = make_event(db, tenant, make_enterprise(db, tenant, "Apollo Events"), title="Community Wellness Walk")
    sent: list[dict] = []

    monkeypatch.setattr(push, "_dispatch", lambda fn, *a, **k: fn(*a, **k))
    claimed: set = set()    # the once-only guard, in memory: the real one commits, and in production it does that on a session of its own
    monkeypatch.setattr(push.notification_idempotency, "claim", lambda _db, key: key not in claimed and not claimed.add(key))
    monkeypatch.setattr(push.notification_idempotency, "release", lambda _db, key: claimed.discard(key))
    monkeypatch.setattr("app.services.notification_service.create_automatic_notification", lambda _db, **kw: sent.append(kw) or SimpleNamespace(id=uuid4()))
    monkeypatch.setattr("app.tasks.event_tasks.expire_waitlist_offer_task.apply_async", lambda *a, **k: None)     # no broker in tests
    monkeypatch.setattr(triggers, "send_emails_via_smtp", lambda msgs: [(False, "off") for _ in msgs])
    emails: list[dict] = []   # what the old email path was asked to do (it runs on a session of its own in production, so it is stubbed here)
    monkeypatch.setattr(triggers, "_safe_notify", lambda db, title, message, category, tenant, metadata, participant_email=None, channels=None, email_result=None, deliver_app=True:
                        emails.append({"category": category, "channels": channels, "deliver_app": deliver_app, "email": participant_email}))

    @contextmanager
    def session_factory():
        yield db

    monkeypatch.setattr("app.db.database.SessionLocal", session_factory)

    def person(name):
        return SimpleNamespace(id=uuid4(), name=name, email=f"{name.lower()}@example.com")

    def register(who, status="confirmed", **extra):
        return make_registration(db, event, who.email, status=status, user_id=who.id, **extra)

    def of(category):
        return [n for n in sent if n["category"] == category]

    return SimpleNamespace(db=db, event=event, tenant=tenant, sent=sent, emails=emails, person=person, register=register, of=of, monkeypatch=monkeypatch)


def assert_flat_strings(note):
    assert note["metadata"].keys() >= KEYS or note["metadata"].keys() >= KEYS - {"category"}
    data = {k: v for k, v in note["metadata"].items() if k != "collapse_key"}
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()), data
    assert data["category"] == note["category"] and data["entity_type"] == "event" and data["entity_id"] == data["event_id"]
    assert note["channels"] == ["in_app", "push"]


# ---- registration ---------------------------------------------------------------------------------------------------------------

def test_registration_confirmed_has_the_routing_payload_and_is_sent_once(env):
    priya = env.person("Priya")
    reg = env.register(priya)

    push.notify_registration_confirmed(env.event, reg)
    push.notify_registration_confirmed(env.event, reg)                    # a retry of the same registration

    [note] = env.of("event_registration_confirmed")
    assert note["user_ids"] == [priya.id] and note["title"] == "Registration confirmed" and note["message"] == "You're registered for Community Wellness Walk"
    assert note["metadata"] == {"category": "event_registration_confirmed", "event_id": str(env.event.id), "registration_id": str(reg.id),
                                "event_name": "Community Wellness Walk", "entity_type": "event", "entity_id": str(env.event.id)}
    assert_flat_strings(note)


def test_nobody_is_told_when_there_is_no_platform_account_for_the_registration(env):
    reg = make_registration(env.db, env.event, "nobody@example.com")

    push.notify_registration_confirmed(env.event, reg)

    assert env.sent == []


def test_the_user_is_found_from_the_same_email_on_another_registration(env):
    priya = env.person("Priya")
    env.register(priya)                                                   # an older registration that knows her id
    other_event = make_event(env.db, env.tenant, title="Other")
    reg = make_registration(env.db, other_event, priya.email)             # no user id on this one

    push.notify_registration_confirmed(other_event, reg)

    assert env.of("event_registration_confirmed")[0]["user_ids"] == [priya.id]


def test_cancelling_a_registration_sends_event_registration_cancelled_and_not_event_cancelled(env):
    from app.services.event_service import cancel_registration_service

    priya = env.person("Priya")
    reg = env.register(priya)

    cancel_registration_service(env.db, env.event.id, reg.id, {"id": str(priya.id), "email": priya.email, "role": "customer"})

    [note] = [n for n in env.sent]
    assert note["category"] == "event_registration_cancelled" and note["message"] == "Your registration for Community Wellness Walk was cancelled"
    assert note["metadata"]["registration_id"] == str(reg.id) and env.of("event_cancelled") == []
    assert_flat_strings(note)
    assert env.emails == [{"category": "event_registration_cancelled", "channels": ["email"], "deliver_app": False, "email": priya.email}]    # the email stays, without its own push


def test_a_free_registration_through_the_service_pushes_confirmed_once(env):
    from app.services.event_service import create_registration_service

    priya = env.person("Priya")
    create_registration_service(env.db, env.event.id, EventRegistrationCreate(participant_name="Priya", participant_email=priya.email),
                                {"id": str(priya.id), "email": priya.email, "role": "customer"}, None)

    assert [n["category"] for n in env.sent] == ["event_registration_confirmed"]
    assert env.sent[0]["user_ids"] == [priya.id]                          # the user id was saved from the verified caller


# ---- waitlist ---------------------------------------------------------------------------------------------------------------------

def test_joining_the_waitlist_pushes_event_waitlist_joined_and_saves_the_user_id(env):
    from app.services.event_service import create_waitlist_entry_service

    env.event.capacity = "1"
    env.db.commit()
    env.register(env.person("Taken"))
    ravi = env.person("Ravi")

    entry = create_waitlist_entry_service(env.db, env.event.id, EventRegistrationCreate(participant_name="Ravi", participant_email=ravi.email),
                                          {"id": str(ravi.id), "email": ravi.email, "role": "customer"}, None)

    assert entry.user_id == ravi.id
    [note] = env.of("event_waitlist_joined")
    assert note["user_ids"] == [ravi.id] and note["title"] == "Waitlist joined" and note["message"] == "You're on the waitlist for Community Wellness Walk"
    assert note["metadata"]["waitlist_id"] == str(entry.id)
    assert_flat_strings(note)


def test_a_free_seat_for_the_waitlist_is_a_confirmed_registration_not_a_second_notification(env):
    from app.services.event_service import cancel_registration_service

    env.event.capacity = "1"
    env.db.commit()
    priya, ravi = env.person("Priya"), env.person("Ravi")
    reg = env.register(priya)
    entry = EventWaitlist(event_id=env.event.id, participant_name="Ravi", participant_email=ravi.email, user_id=ravi.id)
    env.db.add(entry)
    env.db.commit()

    cancel_registration_service(env.db, env.event.id, reg.id, {"id": str(priya.id), "email": priya.email, "role": "customer"})

    assert sorted(n["category"] for n in env.sent) == ["event_registration_cancelled", "event_registration_confirmed"]
    assert env.of("event_registration_confirmed")[0]["user_ids"] == [ravi.id] and env.of("event_waitlist_promoted") == []


def test_a_paid_seat_offer_pushes_event_waitlist_promoted_with_the_waitlist_id(env):
    from app.services.event_service import cancel_registration_service

    env.event.capacity, env.event.pricing_type, env.event.price = "1", "paid", "500"
    env.db.commit()
    priya, ravi = env.person("Priya"), env.person("Ravi")
    reg = env.register(priya)
    entry = EventWaitlist(event_id=env.event.id, participant_name="Ravi", participant_email=ravi.email, user_id=ravi.id)
    env.db.add(entry)
    env.db.commit()

    cancel_registration_service(env.db, env.event.id, reg.id, {"id": str(priya.id), "email": priya.email, "role": "customer"})

    [note] = env.of("event_waitlist_promoted")
    assert note["user_ids"] == [ravi.id] and note["title"] == "Spot available" and note["message"] == "A spot opened for Community Wellness Walk. Register now"
    assert note["metadata"]["waitlist_id"] == str(entry.id) and note["metadata"]["expires_at"].endswith("Z")
    assert env.of("event_registration_confirmed") == []
    assert_flat_strings(note)
    assert [(e["category"], e["deliver_app"]) for e in env.emails if e["email"] == ravi.email] == [("event_waitlist_offer", False)]       # the offer email stays, without a second push


# ---- reminders --------------------------------------------------------------------------------------------------------------------

def test_due_reminder_picks_one_step():
    hours = [24, 1]
    assert push.due_reminder(timedelta(hours=30), hours) is None
    assert push.due_reminder(timedelta(hours=23), hours) == 24
    assert push.due_reminder(timedelta(hours=5), hours) == 24
    assert push.due_reminder(timedelta(minutes=50), hours) == 1
    assert push.due_reminder(timedelta(minutes=-1), hours) is None


def test_starts_at_is_utc_whatever_the_events_time_zone(env):
    env.event.start_date, env.event.time_zone = datetime(2026, 11, 20, 9, 0, 0), "Asia/Kolkata"

    assert push.starts_at_utc(env.event) == "2026-11-20T03:30:00Z"
    env.event.time_zone = "UTC"
    assert push.starts_at_utc(env.event) == "2026-11-20T09:00:00Z"


def test_the_scheduler_sends_each_reminder_once_and_one_per_event_on_the_phone(env):
    priya, ravi, gone = env.person("Priya"), env.person("Ravi"), env.person("Gone")
    env.register(priya)
    env.register(ravi, status="attended")
    env.register(gone, status="cancelled")
    now = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)
    env.event.start_date, env.event.time_zone = datetime(2026, 11, 20, 9, 0), "UTC"            # 23 hours ahead
    env.db.commit()

    first = push.send_due_event_reminders(env.db, now)
    again = push.send_due_event_reminders(env.db, now + timedelta(minutes=5))

    assert first == {"events": 1, "sent": 2} and again["sent"] == 0
    assert {n["user_ids"][0] for n in env.of("event_reminder")} == {priya.id, ravi.id}
    note = env.of("event_reminder")[0]
    assert note["title"] == "Event reminder" and note["message"] == "Community Wellness Walk starts soon"
    assert note["metadata"]["starts_at"] == "2026-11-20T09:00:00Z" and note["metadata"]["kind"] == "24h"
    assert note["metadata"]["collapse_key"] == f"event_reminder:{env.event.id}"
    assert_flat_strings(note)

    last_hour = push.send_due_event_reminders(env.db, datetime(2026, 11, 20, 8, 30, tzinfo=timezone.utc))
    assert last_hour["sent"] == 2 and env.of("event_reminder")[-1]["metadata"]["kind"] == "1h"
    assert len(env.of("event_reminder")) == 4


def test_the_scheduler_ignores_far_events_drafts_and_started_events_and_reminds_again_after_a_reschedule(env):
    env.register(env.person("Priya"))
    now = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)
    env.event.start_date, env.event.time_zone = datetime(2026, 11, 20, 9, 0), "UTC"            # ten days ahead
    env.db.commit()
    assert push.send_due_event_reminders(env.db, now)["sent"] == 0

    near = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
    env.event.status = "draft"
    env.db.commit()
    assert push.send_due_event_reminders(env.db, near)["sent"] == 0
    env.event.status = "published"
    env.db.commit()
    assert push.send_due_event_reminders(env.db, near)["sent"] == 1
    assert push.send_due_event_reminders(env.db, datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc))["sent"] == 0       # already started

    env.event.start_date = datetime(2026, 11, 22, 9, 0)                                         # rescheduled: reminded for the new start
    env.db.commit()
    assert push.send_due_event_reminders(env.db, datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc))["sent"] == 1


def test_the_organisers_remind_button_pushes_a_collapsible_reminder_and_the_email_stays_separate(env):
    env.register(env.person("Priya"))

    summary = triggers.notify_event_reminder(env.db, env.event)

    [note] = env.of("event_reminder")
    assert note["metadata"]["kind"] == "manual" and note["metadata"]["collapse_key"] == f"event_reminder:{env.event.id}"
    assert summary["total"] == 1 and summary["emailed"] == 0                  # the email path is the old one, switched off here


# ---- updates, cancellation, feedback, new events ------------------------------------------------------------------------------------

def test_event_updated_is_pushed_for_schedule_changes_only(env):
    priya, gone = env.person("Priya"), env.person("Gone")
    env.register(priya)
    env.register(gone, status="cancelled")

    push.notify_event_updated(env.event.id, ["title", "category"])
    assert env.sent == []
    triggers.notify_schedule_change(env.db, env.event, {"venue": "Hall A -> Hall B", "title": "x -> y"})

    [note] = env.of("event_updated")
    assert note["user_ids"] == [priya.id] and note["title"] == "Event updated" and note["message"] == "Details changed for Community Wellness Walk"
    assert note["metadata"]["changed"] == "venue" and note["metadata"]["registration_id"]
    assert_flat_strings(note)


def test_event_cancelled_reaches_registered_and_waitlisted_people_once_each(env):
    priya, ravi, waiting, gone = env.person("Priya"), env.person("Ravi"), env.person("Waiting"), env.person("Gone")
    env.register(priya)
    env.register(priya)                                                   # a second registration of the same person
    env.register(ravi, status="attended")
    env.register(gone, status="cancelled")
    env.db.add(EventWaitlist(event_id=env.event.id, participant_name="Waiting", participant_email=waiting.email, user_id=waiting.id))
    env.db.commit()

    triggers.notify_event_cancelled(env.db, env.event, "published")

    notes = env.of("event_cancelled")
    assert sorted(str(n["user_ids"][0]) for n in notes) == sorted(str(u.id) for u in (priya, ravi, waiting))
    assert notes[0]["title"] == "Event cancelled" and notes[0]["message"] == "Community Wellness Walk has been cancelled"
    assert_flat_strings(notes[0])


def test_feedback_is_requested_from_people_who_attended_and_have_not_sent_any(env):
    priya, ravi, ann = env.person("Priya"), env.person("Ravi"), env.person("Ann")
    env.register(priya, status="attended")
    env.register(ravi, status="attended")
    env.register(ann)                                                     # registered but did not attend
    env.db.add(EventFeedback(event_id=env.event.id, participant_email=ravi.email, rating="5"))
    env.db.commit()

    push.notify_feedback_request(env.event.id)

    [note] = env.of("event_feedback_request")
    assert note["user_ids"] == [priya.id] and note["title"] == "Feedback" and note["message"] == "How was Community Wellness Walk? Share feedback"
    assert_flat_strings(note)


def test_new_event_goes_to_the_tenants_users_except_the_publisher_once(env):
    publisher, a, b = env.person("Publisher"), env.person("A"), env.person("B")
    env.monkeypatch.setattr("app.services.invigorate_auth_client.list_tenant_user_ids", lambda tenant, access_token=None: [publisher.id, a.id, b.id])

    push.notify_new_event(env.event.id, exclude_user_id=str(publisher.id))
    push.notify_new_event(env.event.id, exclude_user_id=str(publisher.id))        # republished

    [note] = env.of("event_new")
    assert sorted(map(str, note["user_ids"])) == sorted([str(a.id), str(b.id)])
    assert note["title"] == "New event" and note["message"] == "Community Wellness Walk is now open"
    assert note["metadata"] == {"category": "event_new", "event_id": str(env.event.id), "event_name": "Community Wellness Walk",
                                "entity_type": "event", "entity_id": str(env.event.id)}


def test_a_draft_event_is_not_announced(env):
    env.event.status = "draft"
    env.db.commit()
    env.monkeypatch.setattr("app.services.invigorate_auth_client.list_tenant_user_ids", lambda tenant, access_token=None: [uuid4()])

    push.notify_new_event(env.event.id)

    assert env.sent == []


# ---- what really reaches FCM -----------------------------------------------------------------------------------------------------------

def test_the_real_pipeline_hands_fcm_a_flat_string_map_with_the_collapse_key_outside_the_data(env):
    env.monkeypatch.undo()                                                # use the real create_automatic_notification and delivery service
    db = env.db
    fcm: list[dict] = []
    priya = env.person("Priya")
    db.add(DeviceToken(user_id=priya.id, token="device-token-1", platform="android"))
    db.commit()

    from app.services import notification_delivery_service as delivery

    patches = pytest.MonkeyPatch()                                        # undone at the end, so nothing leaks into other tests
    patches.setattr(delivery, "send_push_to_tokens", lambda tokens, **kw: fcm.append({"tokens": tokens, **kw}) or SimpleNamespace(sent_count=1, failures=[], credentials_error=None))
    patches.setattr(delivery, "_emit_realtime_notification", lambda *a, **k: None)
    from app.services.notification_service import create_automatic_notification

    data = push.payload(str(env.event.id), "Community Wellness Walk", "event_reminder", registration_id=str(uuid4()), starts_at="2026-11-20T09:00:00Z", kind="24h")
    create_automatic_notification(db, title="Event reminder", message="Community Wellness Walk starts soon", category="event_reminder", user_ids=[priya.id],
                                  metadata={**data, "collapse_key": f"event_reminder:{env.event.id}"}, channels=["in_app", "push"])

    [message] = fcm
    assert message["tokens"] == ["device-token-1"] and message["title"] == "Event reminder" and message["body"] == "Community Wellness Walk starts soon"
    assert message["collapse_key"] == f"event_reminder:{env.event.id}" and "conversation_id" not in message      # Android collapse key / iOS thread id
    assert message["data"] == data and "collapse_key" not in message["data"]
    assert all(isinstance(v, str) for v in message["data"].values())
    patches.undo()
    print("FCM_SAMPLE_REMINDER", json.dumps({"notification": {"title": message["title"], "body": message["body"]}, "data": message["data"]}))


def test_payload_drops_none_and_stringifies_everything():
    data = push.payload("e1", "Walk", "event_updated", registration_id=None, count=3, flag=True)

    assert data == {"category": "event_updated", "event_id": "e1", "event_name": "Walk", "entity_type": "event", "entity_id": "e1", "count": "3", "flag": "True"}
