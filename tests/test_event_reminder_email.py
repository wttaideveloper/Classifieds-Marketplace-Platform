"""POST /events/{id}/remind: who is emailed, and an honest answer about whether it worked.

The endpoint used to reply "Reminders sent" whatever happened to the emails (the sender logged a warning and returned
False, and nobody looked). It now says how many people the mail server accepted, how many it did not and why, split
into online and walk-in registrations. The mail server here is a fake, so every way mail can fail is reproduced.
"""
import smtplib
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.database import Base
from app.models.notification_model import Notification, NotificationLog, UserNotification
from app.services import notification_triggers as triggers
from tests.event_sql_support import (
    API, client_for, customer_user, make_enterprise, make_event, make_registration, make_session, reset_overrides,
    staff_user, super_admin_user,
)

TENANT = uuid4()


class Plan:
    """What the fake mail server does in this test."""
    def __init__(self):
        self.connect_error = None
        self.login_error = None
        self.refuse = set()          # recipient addresses it refuses
        self.drop_on_message = None  # 1-based index of the message on which the connection drops (once)
        self.connections = 0
        self.logins = 0
        self.sent = []               # (to, subject, body)
        self._dropped = False


@pytest.fixture
def mail(monkeypatch):
    plan = Plan()

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            if plan.connect_error:
                raise plan.connect_error
            plan.connections += 1
            self.timeout = timeout

        def starttls(self):
            pass

        def login(self, user, password):
            plan.logins += 1
            if plan.login_error:
                raise plan.login_error

        def send_message(self, msg):
            if plan.drop_on_message and len(plan.sent) + 1 == plan.drop_on_message and not plan._dropped:
                plan._dropped = True
                raise smtplib.SMTPServerDisconnected("gone")
            if msg["To"] in plan.refuse:
                raise smtplib.SMTPRecipientsRefused({msg["To"]: (550, b"no such user")})
            plan.sent.append((msg["To"], msg["Subject"], msg.get_content()))

        def quit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(triggers.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(settings, "email_user", "noreply@example.com")
    monkeypatch.setattr(settings, "email_pass", "app-password-123")
    return plan


@pytest.fixture
def world(monkeypatch, mail):
    db = make_session()
    # _safe_notify does its in-app work and writes an audit row on a session of its own, as in production
    Base.metadata.create_all(db.bind, tables=[Notification.__table__, UserNotification.__table__, NotificationLog.__table__])
    own_sessions = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr("app.db.database.SessionLocal", own_sessions)
    monkeypatch.setattr("app.services.notification_service.create_automatic_notification", lambda *a, **k: None)
    enterprise = make_enterprise(db, TENANT)
    event = make_event(
        db, TENANT, enterprise, title="Wellness Day", status="published", delivery_mode="hybrid",
        venue={"name": "City Hall", "address": "1 Main Road", "city": "Pune"}, meeting_link="https://meet.example.com/room",
        start_date=datetime.utcnow() + timedelta(days=1), end_date=datetime.utcnow() + timedelta(days=2),
    )
    yield SimpleNamespace(db=db, event=event, owner=staff_user(TENANT, "admin"), enterprise=enterprise, audit=own_sessions)
    reset_overrides()
    db.close()


_DEFAULT = object()  # "the usual caller"; None means an anonymous request


def remind(world, user=_DEFAULT):
    return client_for(world.db, world.owner if user is _DEFAULT else user).post(f"{API}/{world.event.id}/remind")


def register(world, email, *, walk_in=False, status="confirmed"):
    return make_registration(
        world.db, world.event, email, status=status, registration_source="walk_in" if walk_in else None,
    )


# --- who is emailed ---------------------------------------------------------------------------------------------

def test_online_and_walk_in_registrations_are_both_emailed(world, mail):
    register(world, "online@example.com")
    register(world, "walkin@example.com", walk_in=True)

    resp = remind(world)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert sorted(to for to, _, _ in mail.sent) == ["online@example.com", "walkin@example.com"]
    assert body["total"] == 2 and body["emailed"] == 2 and body["failed"] == 0 and body["skipped"] == 0
    assert body["by_source"]["online"] == {"total": 1, "emailed": 1, "failed": 0, "skipped": 0}
    assert body["by_source"]["walk_in"] == {"total": 1, "emailed": 1, "failed": 0, "skipped": 0}
    assert body["message"] == "Reminder emailed to all 2 registered people." and body["smtp_configured"] is True


def test_attended_registrations_are_included_cancelled_ones_are_not(world, mail):
    register(world, "attended@example.com", status="attended")
    register(world, "cancelled@example.com", status="cancelled")
    register(world, "noshow@example.com", status="no_show")

    remind(world)

    assert [to for to, _, _ in mail.sent] == ["attended@example.com"]


def test_the_email_has_the_event_details_in_plain_words(world, mail):
    register(world, "online@example.com")

    remind(world)

    [(to, subject, text)] = mail.sent
    assert subject == "Reminder: Wellness Day tomorrow"
    assert "City Hall, 1 Main Road, Pune" in text and "{" not in text           # not a dict dump
    assert "https://meet.example.com/room" in text and "None" not in text


def test_an_in_person_event_does_not_print_none_for_the_meeting(world, mail):
    world.event.meeting_link = None
    world.db.commit()
    register(world, "online@example.com")

    remind(world)

    assert "None" not in mail.sent[0][2] and "Join online" not in mail.sent[0][2]


def test_everyone_is_emailed_over_one_connection(world, mail):
    for i in range(6):
        register(world, f"person{i}@example.com", walk_in=i % 2 == 0)

    remind(world)

    assert len(mail.sent) == 6 and mail.connections == 1 and mail.logins == 1


def test_an_address_with_spaces_or_capitals_still_gets_the_email(world, mail):
    register(world, "  Mixed.Case@Example.com ")

    remind(world)

    assert len(mail.sent) == 1 and mail.sent[0][0].strip().lower() == "mixed.case@example.com"


# --- the answer is honest when it did not work -------------------------------------------------------------------

def test_smtp_not_configured_is_reported_not_hidden(world, mail, monkeypatch):
    monkeypatch.setattr(settings, "email_pass", "")
    register(world, "online@example.com")
    register(world, "walkin@example.com", walk_in=True)

    resp = remind(world)

    assert resp.status_code == 201
    body = resp.json()
    assert body["emailed"] == 0 and body["failed"] == 2 and body["smtp_configured"] is False
    assert body["message"].startswith("No reminder email could be sent to the 2 registered people.")
    assert "not configured" in body["message"]
    assert {f["source"] for f in body["failures"]} == {"online", "walk_in"}
    assert mail.sent == []


def test_a_rejected_sign_in_says_so(world, mail):
    mail.login_error = smtplib.SMTPAuthenticationError(535, b"bad credentials")
    register(world, "online@example.com")

    body = remind(world).json()

    assert body["emailed"] == 0 and body["failed"] == 1
    assert "rejected the sign-in" in body["failures"][0]["reason"] and "app password" in body["failures"][0]["reason"]
    assert "app-password-123" not in str(body)  # the password is never returned


def test_an_unreachable_mail_server_says_so(world, mail):
    mail.connect_error = ConnectionRefusedError("blocked")
    register(world, "online@example.com")

    body = remind(world).json()

    assert body["failed"] == 1 and "Could not reach the mail server" in body["failures"][0]["reason"]


def test_one_refused_address_does_not_stop_the_others(world, mail):
    register(world, "good1@example.com")
    register(world, "gone@example.com", walk_in=True)
    register(world, "good2@example.com")
    mail.refuse = {"gone@example.com"}

    body = remind(world).json()

    assert sorted(to for to, _, _ in mail.sent) == ["good1@example.com", "good2@example.com"]
    assert body["emailed"] == 2 and body["failed"] == 1
    assert body["failures"] == [{"email": "gone@example.com", "source": "walk_in", "reason": "The mail server refused this recipient address."}]
    assert body["by_source"]["walk_in"]["failed"] == 1
    assert body["message"] == "Reminder emailed to 2 of 3 registered people; 1 could not be reached."


def test_a_dropped_connection_is_reopened_once(world, mail):
    for i in range(4):
        register(world, f"person{i}@example.com")
    mail.drop_on_message = 2

    body = remind(world).json()

    assert body["emailed"] == 4 and mail.connections == 2


def test_a_registration_without_a_usable_email_is_skipped_and_named(world, mail):
    register(world, "fine@example.com")
    register(world, "not-an-email", walk_in=True)

    body = remind(world).json()

    assert body["total"] == 2 and body["emailed"] == 1 and body["skipped"] == 1
    assert body["failures"] == [{"email": "not-an-email", "source": "walk_in", "reason": "The registration has no valid email address."}]
    assert body["by_source"]["walk_in"]["skipped"] == 1


def test_no_registrations_says_so(world, mail):
    body = remind(world).json()

    assert body["total"] == 0 and body["message"] == "Nobody is registered for this event yet, so no reminder was sent."
    assert mail.connections == 0


def test_only_the_events_managers_can_send_it(world, mail):
    register(world, "online@example.com")

    for user in (None, customer_user("stranger@example.com"), staff_user(uuid4(), "admin")):
        assert remind(world, user).status_code in (401, 403)
    assert mail.sent == []


# --- the single-message sender used by every other notification ----------------------------------------------------

def test_the_single_sender_still_returns_a_bool_and_remembers_why(world, mail):
    mail.login_error = smtplib.SMTPAuthenticationError(535, b"no")

    assert triggers._send_email_via_smtp("a@example.com", "s", "b") is False
    assert "rejected the sign-in" in triggers._last_email_error.get()

    mail.login_error = None
    assert triggers._send_email_via_smtp("a@example.com", "s", "b") is True
    assert triggers._last_email_error.get() is None


def test_the_mail_server_gets_a_timeout(world, mail, monkeypatch):
    seen = {}
    real = triggers.smtplib.SMTP

    class Spy(real):
        def __init__(self, host, port, timeout=None):
            seen["timeout"] = timeout
            super().__init__(host, port, timeout)

    monkeypatch.setattr(triggers.smtplib, "SMTP", Spy)
    triggers._send_email_via_smtp("a@example.com", "s", "b")

    assert seen["timeout"] == triggers.SMTP_TIMEOUT_SECONDS


# --- GET /admin/notifications/email-diagnostics ---------------------------------------------------------------------

DIAG = "/api/v1/admin/notifications/email-diagnostics"


def diag(world, user=_DEFAULT, **params):
    return client_for(world.db, super_admin_user() if user is _DEFAULT else user).get(DIAG, params=params)


def test_the_mail_check_walks_through_each_step(world, mail):
    body = diag(world).json()

    assert body["ok"] is True and body["steps"] == {"connect": "ok", "starttls": "ok", "login": "ok"}
    assert body["host"] == settings.SMTP_HOST and body["from"] == "noreply@example.com"
    assert "app-password-123" not in str(body)


def test_the_mail_check_can_send_a_test_email(world, mail):
    body = diag(world, send_to="me@example.com").json()

    assert body["ok"] is True and body["steps"]["send_test"] == "ok"
    assert [to for to, _, _ in mail.sent] == ["me@example.com"]


def test_the_mail_check_names_the_failing_step(world, mail):
    mail.login_error = smtplib.SMTPAuthenticationError(535, b"no")
    body = diag(world).json()
    assert body["ok"] is False and body["steps"]["login"] == "failed" and "rejected the sign-in" in body["problem"]

    mail.login_error = None
    mail.connect_error = TimeoutError("timed out")
    body = diag(world).json()
    assert body["ok"] is False and body["steps"]["connect"] == "failed" and "Could not reach the mail server" in body["problem"]


def test_the_mail_check_reports_missing_settings(world, mail, monkeypatch):
    monkeypatch.setattr(settings, "email_user", "")

    body = diag(world).json()

    assert body["ok"] is False and body["configured"] is False and "empty" in body["problem"]


def test_the_mail_check_is_for_super_admins_only(world, mail):
    for user in (None, customer_user("c@example.com"), staff_user(TENANT, "admin")):
        assert diag(world, user).status_code in (401, 403)


def test_the_mail_check_refuses_a_bad_test_address(world, mail):
    assert diag(world, send_to="nope").status_code == 422
