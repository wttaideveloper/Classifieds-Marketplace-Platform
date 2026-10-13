"""POST /events/{id}/announcements: is the email really sent, to walk-in and online attendees, and does the answer say so.

Reported: an announcement answered 201 and no email arrived at a walk-in attendee's address. Two causes were found:
the request schema defaulted `channels` to ["in_app"], which silently overrode the service's own default of in-app plus
email (so no email was ever attempted unless the client listed "email"); and `sent_count` counted every recipient
whose send call did not raise, although a failed email only returns False. Walk-ins usually have no app account, so
for them email is the only channel. Uses the fake mail server from test_event_reminder_email.py.
"""
import smtplib

import pytest

from tests.event_sql_support import API, client_for, customer_user, staff_user
from tests.test_event_reminder_email import (  # noqa: F401  (fixtures and helpers are used by name)
    TENANT, mail, register, world,
)


def announce(world, body=None, user=None):
    payload = {"title": "Venue changed", "message": "We have moved to Hall B."} if body is None else body
    return client_for(world.db, world.owner if user is None else user).post(f"{API}/{world.event.id}/announcements", json=payload)


# --- the reported case ---------------------------------------------------------------------------------------------

def test_an_announcement_that_names_no_channels_is_emailed_to_walk_in_and_online_attendees(world, mail):
    register(world, "online@example.com")
    register(world, "walkin@example.com", walk_in=True)

    resp = announce(world)  # no "channels" at all, as the admin screen sends it

    assert resp.status_code == 201, resp.text
    assert sorted(to for to, _, _ in mail.sent) == ["online@example.com", "walkin@example.com"]
    body = resp.json()
    assert body["channels"] == ["in_app", "email"] and body["email_requested"] is True
    assert body["recipient_count"] == 2 and body["emailed"] == 2 and body["sent_count"] == 2
    assert body["by_source"]["walk_in"] == {"total": 1, "emailed": 1, "failed": 0, "skipped": 0}
    assert body["by_source"]["online"] == {"total": 1, "emailed": 1, "failed": 0, "skipped": 0}
    assert body["delivery_summary"] == "Emailed to all 2 registered people."


def test_the_email_has_the_title_and_the_message(world, mail):
    register(world, "walkin@example.com", walk_in=True)

    announce(world)

    [(to, subject, text)] = mail.sent
    assert subject == "Venue changed" and "We have moved to Hall B." in text


def test_a_missing_title_falls_back_to_the_event_name(world, mail):
    register(world, "walkin@example.com", walk_in=True)

    announce(world, {"message": "Doors open at 9."})

    assert mail.sent[0][1] == "Announcement: Wellness Day"


def test_everyone_is_emailed_over_one_connection(world, mail):
    for i in range(5):
        register(world, f"person{i}@example.com", walk_in=i % 2 == 0)

    announce(world)

    assert len(mail.sent) == 5 and mail.connections == 1 and mail.logins == 1


# --- channels the manager chose ------------------------------------------------------------------------------------

def test_an_explicit_in_app_only_announcement_sends_no_email_and_says_so(world, mail):
    register(world, "walkin@example.com", walk_in=True)

    body = announce(world, {"message": "Hi", "channels": ["in_app"]}).json()

    assert mail.connections == 0 and mail.sent == []
    assert body["email_requested"] is False and body["emailed"] == 0
    assert body["delivery_summary"] == "Sent in the app only; no email was requested (channels: in_app)."


def test_an_email_only_announcement_works(world, mail):
    register(world, "walkin@example.com", walk_in=True)

    body = announce(world, {"message": "Hi", "channels": ["email"]}).json()

    assert [to for to, _, _ in mail.sent] == ["walkin@example.com"] and body["emailed"] == 1


# --- the answer is honest ------------------------------------------------------------------------------------------

def test_smtp_not_configured_is_reported_and_nobody_is_counted_as_sent(world, mail, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "email_pass", "")
    register(world, "online@example.com")
    register(world, "walkin@example.com", walk_in=True)

    body = announce(world).json()

    assert body["recipient_count"] == 2 and body["emailed"] == 0 and body["failed"] == 2
    assert body["sent_count"] == 0  # it used to say 2
    assert body["smtp_configured"] is False and "not configured" in body["delivery_summary"]
    assert body["delivery_summary"].startswith("No email could be sent to the 2 registered people.")
    assert {f["source"] for f in body["failures"]} == {"online", "walk_in"}


def test_a_rejected_sign_in_is_reported(world, mail):
    mail.login_error = smtplib.SMTPAuthenticationError(535, b"no")
    register(world, "walkin@example.com", walk_in=True)

    body = announce(world).json()

    assert body["emailed"] == 0 and body["sent_count"] == 0
    assert "rejected the sign-in" in body["failures"][0]["reason"]


def test_one_refused_address_does_not_stop_the_rest(world, mail):
    register(world, "good@example.com")
    register(world, "gone@example.com", walk_in=True)
    mail.refuse = {"gone@example.com"}

    body = announce(world).json()

    assert [to for to, _, _ in mail.sent] == ["good@example.com"]
    assert body["emailed"] == 1 and body["failed"] == 1 and body["sent_count"] == 1
    assert body["failures"] == [{"email": "gone@example.com", "source": "walk_in", "reason": "The mail server refused this recipient address."}]
    assert body["delivery_summary"] == "Emailed 1 of 2 registered people; 1 could not be reached."


def test_a_registration_without_a_usable_address_is_skipped_and_named(world, mail):
    register(world, "fine@example.com")
    register(world, "not-an-email", walk_in=True)

    body = announce(world).json()

    assert body["emailed"] == 1 and body["skipped"] == 1
    assert body["failures"][0]["reason"] == "The registration has no valid email address."


def test_cancelled_and_no_show_registrations_are_left_out(world, mail):
    register(world, "in@example.com", status="attended")
    register(world, "cancelled@example.com", status="cancelled")
    register(world, "noshow@example.com", status="no_show")

    body = announce(world).json()

    assert [to for to, _, _ in mail.sent] == ["in@example.com"] and body["recipient_count"] == 1


def test_no_registrations_says_so(world, mail):
    body = announce(world).json()

    assert body["recipient_count"] == 0 and body["delivery_summary"] == "Nobody is registered for this event yet."
    assert mail.connections == 0


def test_the_existing_response_fields_are_all_still_there(world, mail):
    register(world, "walkin@example.com", walk_in=True)

    body = announce(world).json()

    for key in ("id", "event_id", "sent_by", "recipient_count", "sent_count", "created_at", "title", "message"):
        assert key in body
    assert body["title"] == "Venue changed" and body["message"] == "We have moved to Hall B."


def test_only_the_events_managers_can_send_an_announcement(world, mail):
    register(world, "walkin@example.com", walk_in=True)

    for user in (customer_user("stranger@example.com"), staff_user(__import__("uuid").uuid4(), "admin")):
        assert announce(world, user=user).status_code in (401, 403)
    assert mail.sent == []
