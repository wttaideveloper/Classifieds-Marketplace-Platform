"""Virtual meeting providers: platform switches, connecting, settings, generating / pasting a link for an event, and what customers see.

The provider adapters are replaced by a fake: nothing here talks to Zoom, Google or Microsoft.
"""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest

from app.models.meeting_model import MeetingAudit, MeetingConnection, MeetingLink
from app.services import meeting_crypto, meeting_service
from app.services.meeting_providers.base import Account, MeetingResult, ProviderAuthError, ProviderError, Tokens
from event_sql_support import API, client_for, customer_user, make_enterprise, make_event, make_registration, make_session, reset_overrides, staff_user, super_admin_user

M = "/api/v1/meetings"


class FakeProvider:
    """Stands in for Zoom / Google Meet / Teams. `script` is a list of exceptions to raise on the next create calls."""

    def __init__(self, key):
        self.key, self.label, self.is_configured = key, key.title(), True
        self.calls, self.script, self.counter = [], [], 0
        self.refresh_error = None

    def configured(self):
        return self.is_configured

    def authorize_url(self, state, redirect_uri):
        return f"https://provider.test/{self.key}/authorize?state={state}&redirect_uri={redirect_uri}"

    def exchange_code(self, code, redirect_uri):
        self.calls.append(("exchange", code))
        if code == "bad":
            raise ProviderError("The code was refused", code="provider_rejected")
        return Tokens("access-secret-1", "refresh-secret-1", datetime.utcnow() + timedelta(hours=1), "meetings"), Account("acct-1", "host@example.com", "Host Person")

    def refresh(self, refresh_token):
        self.calls.append(("refresh", refresh_token))
        if self.refresh_error:
            raise self.refresh_error
        return Tokens("access-secret-2", "refresh-secret-1", datetime.utcnow() + timedelta(hours=1), "meetings")

    def create(self, tokens, spec):
        self.calls.append(("create", tokens.access_token, spec.title, spec.start_utc, spec.end_utc))
        if self.script:
            raise self.script.pop(0)
        self.counter += 1
        return MeetingResult(f"ext-{self.counter}", f"https://meet.test/{self.key}/{self.counter}", passcode="123456", dial_in={"number": "+1 555 0100"})

    def update(self, tokens, external_id, spec):
        self.calls.append(("update", external_id, spec.start_utc, spec.title))

    def delete(self, tokens, external_id):
        self.calls.append(("delete", external_id))

    def revoke(self, tokens):
        self.calls.append(("revoke",))


@pytest.fixture
def env(monkeypatch):
    for target in (
        "app.services.super_admin_identity.fetch_internal_user_by_id", "app.services.super_admin_identity.fetch_auth_me_profile",
        "app.services.invigorate_auth_client.fetch_tenant_me_profile", "app.services.invigorate_auth_client.fetch_auth_me_profile",
    ):
        monkeypatch.setattr(target, lambda *a, **k: None)
    monkeypatch.setattr("app.core.auth_context.resolve_auth_tenant_id_with_db", lambda _db, user, access_token=None: user.get("tenant_id"))
    db = make_session()
    for model in (MeetingConnection, MeetingLink, MeetingAudit):
        model.__table__.create(bind=db.get_bind(), checkfirst=True)
    from app.models.meeting_model import MeetingMerchantSettings, MeetingProviderSetting
    for model in (MeetingMerchantSettings, MeetingProviderSetting):
        model.__table__.create(bind=db.get_bind(), checkfirst=True)

    providers = {key: FakeProvider(key) for key in ("zoom", "google_meet", "teams")}
    monkeypatch.setattr(meeting_service, "get_provider", lambda key: providers[key])
    sent = []
    monkeypatch.setattr(meeting_service, "_dispatch", lambda fn, *a, **k: sent.append((fn.__name__, a)))
    monkeypatch.setattr(meeting_service.time, "sleep", lambda *_: None)

    tenant = uuid4()
    enterprise = make_enterprise(db, tenant, "Calm Clinic")
    start = datetime.utcnow() + timedelta(days=3)
    event = make_event(db, tenant, enterprise, title="Online Yoga", status="published", delivery_mode="online", time_zone="UTC",
                       start_date=start, end_date=start + timedelta(hours=2))
    admin = staff_user(tenant, "admin")
    yield SimpleNamespace(db=db, tenant=tenant, enterprise=enterprise, event=event, providers=providers, sent=sent, admin=admin,
                          provider_user=staff_user(tenant, "provider"), other=staff_user(uuid4(), "admin"), root=super_admin_user(), start=start)
    reset_overrides()
    db.close()


def as_user(env, user):
    return client_for(env.db, user)


def connect(env, key="zoom", user=None):
    """The whole OAuth round trip with the fake provider. Returns the callback response."""
    client = as_user(env, user or env.admin)
    start = client.post(f"{M}/providers/{key}/connect")
    assert start.status_code == 200, start.text
    state = parse_qs(urlparse(start.json()["authorization_url"]).query)["state"][0]
    return client.get(f"{M}/oauth/callback/{key}", params={"code": "good", "state": state}, follow_redirects=False)


def notified(env, name):
    return [a for fn, a in env.sent if fn == name]


# ---------------------------------------------------------------- platform switches

def test_the_super_admin_lists_and_switches_providers_and_businesses_are_told(env):
    connect(env, "zoom")
    root = as_user(env, env.root)

    listed = root.get(f"{M}/admin/providers").json()["items"]
    assert [p["provider"] for p in listed] == ["zoom", "google_meet", "teams"] and all(p["enabled"] and p["configured"] for p in listed)
    assert next(p for p in listed if p["provider"] == "zoom")["connected_businesses"] == 1

    off = root.put(f"{M}/admin/providers/zoom", json={"enabled": False})
    assert off.status_code == 200 and off.json()["enabled"] is False
    [(category_args)] = [a for a in notified(env, "_notify_users")]
    assert category_args[1] == "meeting_provider_disabled" and category_args[0] == [uuid_of(env.admin)]
    assert as_user(env, env.admin).put(f"{M}/admin/providers/zoom", json={"enabled": True}).status_code == 403
    assert as_user(env, env.admin).get(f"{M}/admin/providers").status_code == 403


def uuid_of(user):
    from uuid import UUID
    return UUID(user["id"])


# ---------------------------------------------------------------- connecting

def test_a_merchant_sees_each_provider_and_connects_one(env):
    client = as_user(env, env.admin)
    before = client.get(f"{M}/providers").json()
    assert [(p["provider"], p["status"], p["can_connect"]) for p in before["items"]] == [("zoom", "not_connected", True), ("google_meet", "not_connected", True), ("teams", "not_connected", True)]

    response = connect(env, "zoom")

    assert response.status_code == 302 and "meeting_connected=true" in response.headers["location"] and "/settings/meetings" in response.headers["location"]
    zoom = next(p for p in client.get(f"{M}/providers").json()["items"] if p["provider"] == "zoom")
    assert zoom["status"] == "connected" and zoom["account_email"] == "host@example.com" and zoom["account_name"] == "Host Person"
    row = env.db.query(MeetingConnection).one()
    assert row.tenant_id == env.tenant and row.connected_by == uuid_of(env.admin)
    assert "access-secret-1" not in row.access_token and "refresh-secret-1" not in row.refresh_token            # stored encrypted
    assert meeting_crypto.decrypt(row.access_token) == "access-secret-1"
    assert "secret" not in json.dumps(client.get(f"{M}/providers").json()) and "secret" not in json.dumps(client.get(f"{M}/audit").json())


def test_only_an_enterprise_admin_connects(env):
    for user in (env.provider_user, customer_user("c@example.com"), env.root):
        assert as_user(env, user).post(f"{M}/providers/zoom/connect").status_code == 403
        assert as_user(env, user).get(f"{M}/providers").status_code == 403
    assert as_user(env, None).get(f"{M}/providers").status_code == 401


def test_a_provider_that_is_off_or_not_set_up_cannot_be_connected(env):
    env.providers["teams"].is_configured = False
    as_user(env, env.root).put(f"{M}/admin/providers/zoom", json={"enabled": False})
    client = as_user(env, env.admin)

    assert client.post(f"{M}/providers/zoom/connect").status_code == 409 and "switched off" in client.post(f"{M}/providers/zoom/connect").json()["detail"]
    assert client.post(f"{M}/providers/teams/connect").status_code == 409 and "not set up" in client.post(f"{M}/providers/teams/connect").json()["detail"]
    items = {p["provider"]: p for p in client.get(f"{M}/providers").json()["items"]}
    assert items["zoom"]["can_connect"] is False and items["teams"]["can_connect"] is False and items["google_meet"]["can_connect"] is True


def test_the_callback_refuses_a_forged_expired_or_denied_connection(env):
    client = as_user(env, env.admin)

    forged = client.get(f"{M}/oauth/callback/zoom", params={"code": "good", "state": "abc.def"}, follow_redirects=False)
    assert "meeting_connected=false" in forged.headers["location"] and "reason=invalid_state" in forged.headers["location"]
    other_provider = client.post(f"{M}/providers/zoom/connect").json()["authorization_url"]
    state = parse_qs(urlparse(other_provider).query)["state"][0]
    wrong = client.get(f"{M}/oauth/callback/teams", params={"code": "good", "state": state}, follow_redirects=False)
    assert "reason=invalid_state" in wrong.headers["location"]
    denied = client.get(f"{M}/oauth/callback/zoom", params={"error": "access_denied", "state": state}, follow_redirects=False)
    assert "reason=denied" in denied.headers["location"]
    bad = client.get(f"{M}/oauth/callback/zoom", params={"code": "bad", "state": state}, follow_redirects=False)
    assert "meeting_connected=false" in bad.headers["location"] and "provider_rejected" in bad.headers["location"]
    assert env.db.query(MeetingConnection).count() == 0
    old = meeting_crypto.sign_state({"t": str(env.tenant), "u": env.admin["id"], "p": "zoom"})
    meeting_crypto.STATE_LIFETIME_SECONDS, saved = -5, meeting_crypto.STATE_LIFETIME_SECONDS
    try:
        stale = meeting_crypto.sign_state({"t": str(env.tenant), "u": env.admin["id"], "p": "zoom"})
    finally:
        meeting_crypto.STATE_LIFETIME_SECONDS = saved
    assert meeting_crypto.read_state(stale) is None and meeting_crypto.read_state(old) is not None


def test_disconnecting_deletes_the_tokens_and_revokes(env):
    connect(env, "zoom")

    resp = as_user(env, env.admin).delete(f"{M}/providers/zoom")

    assert resp.json() == {"provider": "zoom", "status": "not_connected"} and env.db.query(MeetingConnection).count() == 0
    assert ("revoke",) in env.providers["zoom"].calls
    assert [a.action for a in env.db.query(MeetingAudit).order_by(MeetingAudit.created_at).all()] == ["connected", "disconnected"]
    assert as_user(env, env.admin).delete(f"{M}/providers/zoom").json()["status"] == "not_connected"


def test_the_default_provider_must_be_connected(env):
    client = as_user(env, env.admin)
    assert client.put(f"{M}/settings", json={"default_provider": "zoom"}).status_code == 409
    connect(env, "zoom")

    assert client.put(f"{M}/settings", json={"default_provider": "zoom"}).json() == {"default_provider": "zoom", "auto_generate": True}
    assert client.put(f"{M}/settings", json={"auto_generate": False}).json() == {"default_provider": "zoom", "auto_generate": False}
    assert client.get(f"{M}/settings").json() == {"default_provider": "zoom", "auto_generate": False}
    assert next(p for p in client.get(f"{M}/providers").json()["items"] if p["provider"] == "zoom")["is_default"] is True
    assert client.put(f"{M}/settings", json={"default_provider": None}).json()["default_provider"] is None
    assert client.put(f"{M}/settings", json={"default_provider": "skype"}).status_code == 422


# ---------------------------------------------------------------- an event's meeting

def test_generating_creates_the_meeting_and_puts_the_link_on_the_event(env):
    connect(env, "zoom")
    make_registration(env.db, env.event, "cust@example.com")
    client = as_user(env, env.admin)
    assert client.put(f"{M}/events/{env.event.id}", json={"provider": "zoom"}).status_code == 200

    resp = client.post(f"{M}/events/{env.event.id}/generate")

    body = resp.json()
    assert resp.status_code == 200 and body["status"] == "created" and body["meeting_link"] == "https://meet.test/zoom/1" and body["link_source"] == "generated"
    assert body["passcode"] == "123456" and body["dial_in"] == {"number": "+1 555 0100"} and body["provider_label"] == "Zoom" and body["needs_attention"] is False
    env.db.refresh(env.event)
    assert env.event.meeting_link == "https://meet.test/zoom/1" and env.event.meeting_provider == "zoom"
    create = next(c for c in env.providers["zoom"].calls if c[0] == "create")
    assert create[1] == "access-secret-1" and create[2] == "Online Yoga" and create[4] - create[3] == timedelta(hours=2)
    assert any(a[1] == "meeting_link_added" or a[1:2] == ("meeting_link_added",) for _, a in [(f, x) for f, x in env.sent] if len(a) > 1) or notified(env, "_notify_participants")
    assert client.post(f"{M}/events/{env.event.id}/generate").json()["meeting_link"] == "https://meet.test/zoom/1"                  # not created twice
    assert sum(1 for c in env.providers["zoom"].calls if c[0] == "create") == 1


def test_a_pasted_link_wins_and_can_be_removed(env):
    connect(env, "zoom")
    client = as_user(env, env.admin)
    client.put(f"{M}/events/{env.event.id}", json={"provider": "zoom"})
    client.post(f"{M}/events/{env.event.id}/generate")

    pasted = client.put(f"{M}/events/{env.event.id}", json={"manual_link": "https://example.com/my-room"}).json()

    assert pasted["meeting_link"] == "https://example.com/my-room" and pasted["link_source"] == "manual" and pasted["status"] == "manual"
    env.db.refresh(env.event)
    assert env.event.meeting_link == "https://example.com/my-room"
    assert notified(env, "_notify_participants")                                                                               # customers are told it changed
    assert client.post(f"{M}/events/{env.event.id}/generate").json()["meeting_link"] == "https://example.com/my-room"          # generation leaves it alone
    back = client.put(f"{M}/events/{env.event.id}", json={"manual_link": None}).json()
    assert back["meeting_link"] == "https://meet.test/zoom/1" and back["link_source"] == "generated"
    assert client.put(f"{M}/events/{env.event.id}", json={"manual_link": "javascript:alert(1)"}).status_code == 422


def test_a_link_typed_into_the_event_form_counts_as_pasted(env):
    connect(env, "zoom")
    env.event.meeting_link = "https://zoom.us/j/9999999999"
    env.db.commit()
    client = as_user(env, env.admin)
    client.put(f"{M}/events/{env.event.id}", json={"provider": "zoom"})

    body = client.post(f"{M}/events/{env.event.id}/generate").json()

    assert body["link_source"] == "manual" and body["meeting_link"] == "https://zoom.us/j/9999999999"
    assert not [c for c in env.providers["zoom"].calls if c[0] == "create"]


def test_an_in_person_event_has_no_meeting_settings(env):
    env.event.delivery_mode = "in_person"
    env.db.commit()
    client = as_user(env, env.admin)

    assert client.get(f"{M}/events/{env.event.id}").json()["virtual"] is False
    assert client.put(f"{M}/events/{env.event.id}", json={"provider": "zoom"}).status_code == 422
    assert client.post(f"{M}/events/{env.event.id}/generate").status_code == 422


def test_generate_explains_what_is_missing(env):
    client = as_user(env, env.admin)
    assert client.post(f"{M}/events/{env.event.id}/generate").status_code == 409 and "Choose a meeting provider" in client.post(f"{M}/events/{env.event.id}/generate").json()["detail"]
    client.put(f"{M}/events/{env.event.id}", json={"provider": "zoom"})
    assert "Connect Zoom first" in client.post(f"{M}/events/{env.event.id}/generate").json()["detail"]
    connect(env, "zoom")
    as_user(env, env.root).put(f"{M}/admin/providers/zoom", json={"enabled": False})
    client = as_user(env, env.admin)
    assert "switched off" in client.post(f"{M}/events/{env.event.id}/generate").json()["detail"]


def test_a_provider_error_is_recorded_retried_and_shown_not_raised(env):
    connect(env, "zoom")
    client = as_user(env, env.admin)
    client.put(f"{M}/events/{env.event.id}", json={"provider": "zoom"})
    env.providers["zoom"].script = [ProviderError("Your Zoom plan has no meetings left", code="provider_rejected")]

    failed = client.post(f"{M}/events/{env.event.id}/generate").json()

    assert failed["status"] == "failed" and failed["needs_attention"] is True and failed["last_error"] == "Your Zoom plan has no meetings left" and failed["attempts"] == 1
    assert failed["meeting_link"] is None
    [(args)] = notified(env, "_notify_users")[-1:]
    assert args[1] == "meeting_link_failed" and args[0] == [uuid_of(env.admin)]
    retried = client.post(f"{M}/events/{env.event.id}/generate").json()                                                # the Retry button
    assert retried["status"] == "created" and retried["attempts"] == 0 and retried["meeting_link"] == "https://meet.test/zoom/1"


def test_a_passing_hiccup_is_retried_once_straight_away(env):
    connect(env, "zoom")
    client = as_user(env, env.admin)
    client.put(f"{M}/events/{env.event.id}", json={"provider": "zoom"})
    env.providers["zoom"].script = [ProviderError("busy", code="provider_unavailable", retryable=True)]

    assert client.post(f"{M}/events/{env.event.id}/generate").json()["status"] == "created"
    assert sum(1 for c in env.providers["zoom"].calls if c[0] == "create") == 2


def test_the_background_job_retries_failed_links_a_limited_number_of_times(env):
    connect(env, "zoom")
    client = as_user(env, env.admin)
    client.put(f"{M}/events/{env.event.id}", json={"provider": "zoom"})
    env.providers["zoom"].script = [ProviderError("no", code="provider_rejected")]
    client.post(f"{M}/events/{env.event.id}/generate")
    row = env.db.query(MeetingLink).one()
    row.updated_at = datetime.utcnow() - timedelta(minutes=10)
    env.db.commit()

    done = meeting_service.retry_failed(env.db)

    assert done == {"retried": 1, "created": 1} and env.db.query(MeetingLink).one().status == "created"
    row.status, row.attempts, row.updated_at = "failed", 5, datetime.utcnow() - timedelta(minutes=10)
    env.db.commit()
    assert meeting_service.retry_failed(env.db) == {"retried": 0, "created": 0}                                         # gave up after 5


def test_an_expired_token_is_refreshed_and_a_withdrawn_one_asks_to_reconnect(env):
    connect(env, "zoom")
    client = as_user(env, env.admin)
    client.put(f"{M}/events/{env.event.id}", json={"provider": "zoom"})
    conn = env.db.query(MeetingConnection).one()
    conn.token_expires_at = datetime.utcnow() - timedelta(minutes=1)
    env.db.commit()

    assert client.post(f"{M}/events/{env.event.id}/generate").json()["status"] == "created"
    assert ("refresh", "refresh-secret-1") in env.providers["zoom"].calls
    assert [c for c in env.providers["zoom"].calls if c[0] == "create"][0][1] == "access-secret-2"

    client.delete(f"{M}/events/{env.event.id}/generated")
    conn = env.db.query(MeetingConnection).one()
    conn.token_expires_at = datetime.utcnow() - timedelta(minutes=1)
    env.db.commit()
    env.providers["zoom"].refresh_error = ProviderAuthError()

    again = client.post(f"{M}/events/{env.event.id}/generate").json()

    assert again["status"] == "failed" and again["connection_status"] == "reconnect_needed"
    assert next(p for p in client.get(f"{M}/providers").json()["items"] if p["provider"] == "zoom")["status"] == "reconnect_needed"
    assert any(a[1] == "meeting_reconnect_needed" for a in notified(env, "_notify_users"))
    assert "Reconnect Zoom first" in client.post(f"{M}/events/{env.event.id}/generate").json()["detail"]


def test_publishing_creates_the_link_automatically_and_cancelling_removes_it(env):
    connect(env, "zoom")
    client = as_user(env, env.admin)
    client.put(f"{M}/settings", json={"default_provider": "zoom"})
    env.event.status = "draft"
    env.db.commit()

    env.event.status = "published"
    meeting_service.on_event_status(env.db, env.event, "draft", "published")
    state = client.get(f"{M}/events/{env.event.id}").json()
    assert state["status"] == "created" and state["provider"] == "zoom" and state["meeting_link"] == "https://meet.test/zoom/1"

    env.event.status = "cancelled"
    meeting_service.on_event_status(env.db, env.event, "published", "cancelled")
    after = client.get(f"{M}/events/{env.event.id}").json()
    assert ("delete", "ext-1") in env.providers["zoom"].calls and after["status"] == "none" and after["meeting_link"] is None
    env.db.refresh(env.event)
    assert env.event.meeting_link is None


def test_publishing_does_nothing_when_automatic_links_are_off_or_nothing_is_connected(env):
    client = as_user(env, env.admin)
    meeting_service.on_event_status(env.db, env.event, "draft", "published")
    assert client.get(f"{M}/events/{env.event.id}").json()["status"] == "none"

    connect(env, "zoom")
    client.put(f"{M}/settings", json={"default_provider": "zoom", "auto_generate": False})
    meeting_service.on_event_status(env.db, env.event, "draft", "published")
    assert client.get(f"{M}/events/{env.event.id}").json()["status"] == "none"
    assert not [c for c in env.providers["zoom"].calls if c[0] == "create"]


def test_a_pasted_link_survives_cancelling(env):
    connect(env, "zoom")
    client = as_user(env, env.admin)
    client.put(f"{M}/events/{env.event.id}", json={"provider": "zoom", "manual_link": "https://example.com/room"})
    meeting_service.on_event_status(env.db, env.event, "published", "cancelled")

    assert client.get(f"{M}/events/{env.event.id}").json()["meeting_link"] == "https://example.com/room"
    assert not [c for c in env.providers["zoom"].calls if c[0] == "delete"]


def test_rescheduling_moves_the_meeting_and_keeps_the_link(env):
    connect(env, "zoom")
    client = as_user(env, env.admin)
    client.put(f"{M}/events/{env.event.id}", json={"provider": "zoom"})
    client.post(f"{M}/events/{env.event.id}/generate")
    env.event.start_date = env.start + timedelta(days=1)
    env.event.end_date = env.start + timedelta(days=1, hours=2)
    env.db.commit()

    meeting_service.on_event_updated(env.db, env.event, {"start_date", "end_date"})

    update = next(c for c in env.providers["zoom"].calls if c[0] == "update")
    assert update[1] == "ext-1" and update[2] == env.start + timedelta(days=1)
    assert client.get(f"{M}/events/{env.event.id}").json()["meeting_link"] == "https://meet.test/zoom/1"
    meeting_service.on_event_updated(env.db, env.event, {"category"})
    assert sum(1 for c in env.providers["zoom"].calls if c[0] == "update") == 1


def test_the_event_time_zone_is_respected(env):
    connect(env, "zoom")
    env.event.time_zone = "Asia/Kolkata"
    env.event.start_date, env.event.end_date = datetime(2026, 11, 20, 12, 30), datetime(2026, 11, 20, 14, 0)
    env.db.commit()
    client = as_user(env, env.admin)
    client.put(f"{M}/events/{env.event.id}", json={"provider": "zoom"})
    client.post(f"{M}/events/{env.event.id}/generate")

    create = next(c for c in env.providers["zoom"].calls if c[0] == "create")

    assert create[3] == datetime(2026, 11, 20, 7, 0) and create[4] == datetime(2026, 11, 20, 8, 30)


def test_deleting_the_event_deletes_its_meeting_and_a_copy_does_not_share_it(env):
    connect(env, "zoom")
    client = as_user(env, env.admin)
    client.put(f"{M}/events/{env.event.id}", json={"provider": "zoom"})
    client.post(f"{M}/events/{env.event.id}/generate")
    env.db.refresh(env.event)
    assert meeting_service.is_generated_link(env.db, env.event) is True

    meeting_service.on_event_deleted(env.db, env.event)

    assert ("delete", "ext-1") in env.providers["zoom"].calls and meeting_service.is_generated_link(env.db, env.event) is False


def test_the_delete_endpoint_removes_only_the_generated_meeting(env):
    connect(env, "zoom")
    client = as_user(env, env.admin)
    client.put(f"{M}/events/{env.event.id}", json={"provider": "zoom"})
    client.post(f"{M}/events/{env.event.id}/generate")

    body = client.delete(f"{M}/events/{env.event.id}/generated").json()

    assert body["status"] == "none" and body["meeting_link"] is None and ("delete", "ext-1") in env.providers["zoom"].calls


# ---------------------------------------------------------------- who may manage an event's meeting

def test_only_the_owning_business_manages_an_events_meeting(env):
    assert as_user(env, env.provider_user).get(f"{M}/events/{env.event.id}").status_code == 200
    assert as_user(env, env.provider_user).put(f"{M}/events/{env.event.id}", json={"auto_generate": False}).status_code == 200
    assert as_user(env, env.other).get(f"{M}/events/{env.event.id}").status_code == 404
    assert as_user(env, env.other).post(f"{M}/events/{env.event.id}/generate").status_code == 404
    assert as_user(env, customer_user("c@example.com")).get(f"{M}/events/{env.event.id}").status_code == 403
    assert as_user(env, env.root).get(f"{M}/events/{env.event.id}").status_code == 200
    assert as_user(env, env.root).put(f"{M}/events/{env.event.id}", json={"auto_generate": False}).status_code == 403
    assert as_user(env, env.admin).get(f"{M}/events/{uuid4()}").status_code == 404


# ---------------------------------------------------------------- customers

def test_a_registered_customer_gets_the_link_and_the_join_window(env, monkeypatch):
    connect(env, "zoom")
    make_registration(env.db, env.event, "cust@example.com")
    client = as_user(env, env.admin)
    client.put(f"{M}/events/{env.event.id}", json={"provider": "zoom"})
    client.post(f"{M}/events/{env.event.id}/generate")
    customer = as_user(env, customer_user("Cust@Example.com"))

    early = customer.get(f"{M}/my/events/{env.event.id}").json()
    assert early["link_ready"] is True and early["meeting_link"] == "https://meet.test/zoom/1" and early["can_join_now"] is False
    assert early["provider"] == "zoom" and early["provider_label"] == "Zoom" and early["passcode"] == "123456"
    assert early["starts_at"].endswith("Z") and early["join_opens_at"] != early["starts_at"]
    assert "host_url" not in json.dumps(early) and "access-secret" not in json.dumps(early)

    monkeypatch.setattr(meeting_service, "_now", lambda: env.start - timedelta(minutes=10))
    assert customer.get(f"{M}/my/events/{env.event.id}").json()["can_join_now"] is True
    monkeypatch.setattr(meeting_service, "_now", lambda: env.start + timedelta(hours=1))
    assert customer.get(f"{M}/my/events/{env.event.id}").json()["can_join_now"] is True
    monkeypatch.setattr(meeting_service, "_now", lambda: env.start + timedelta(hours=3))
    assert customer.get(f"{M}/my/events/{env.event.id}").json()["can_join_now"] is False


def test_the_customer_sees_nothing_until_a_link_exists_and_a_pasted_link_wins(env):
    make_registration(env.db, env.event, "cust@example.com")
    customer = as_user(env, customer_user("cust@example.com"))

    none_yet = customer.get(f"{M}/my/events/{env.event.id}").json()
    assert none_yet["link_ready"] is False and none_yet["meeting_link"] is None and none_yet["can_join_now"] is False

    as_user(env, env.admin).put(f"{M}/events/{env.event.id}", json={"manual_link": "https://example.com/room"})
    customer = as_user(env, customer_user("cust@example.com"))
    assert customer.get(f"{M}/my/events/{env.event.id}").json()["meeting_link"] == "https://example.com/room"


def test_only_a_confirmed_registration_of_a_visible_event_sees_the_link(env):
    as_user(env, env.admin).put(f"{M}/events/{env.event.id}", json={"manual_link": "https://example.com/room"})
    make_registration(env.db, env.event, "pending@example.com", status="pending")
    make_registration(env.db, env.event, "cancelled@example.com", status="cancelled")
    make_registration(env.db, env.event, "attended@example.com", status="attended")

    for email in ("pending@example.com", "cancelled@example.com", "stranger@example.com"):
        assert as_user(env, customer_user(email)).get(f"{M}/my/events/{env.event.id}").status_code == 404
    assert as_user(env, customer_user("attended@example.com")).get(f"{M}/my/events/{env.event.id}").status_code == 200
    env.event.status = "cancelled"
    env.db.commit()
    assert as_user(env, customer_user("attended@example.com")).get(f"{M}/my/events/{env.event.id}").status_code == 404
    assert as_user(env, None).get(f"{M}/my/events/{env.event.id}").status_code == 401
    assert as_user(env, customer_user("a@example.com")).get(f"{M}/my/events/{uuid4()}").status_code == 404


# ---------------------------------------------------------------- audit and the older placeholder links

def test_the_audit_is_scoped_to_the_business_and_holds_no_token(env):
    connect(env, "zoom")
    other_tenant_conn = connect(env, "zoom", user=env.other)

    mine = as_user(env, env.admin).get(f"{M}/audit").json()
    everything = as_user(env, env.root).get(f"{M}/audit").json()

    assert other_tenant_conn.status_code == 302 and mine["total"] == 1 and mine["items"][0]["action"] == "connected" and everything["total"] == 2
    assert "secret" not in json.dumps(everything)
    assert as_user(env, env.root).get(f"{M}/audit", params={"tenant_id": str(env.tenant)}).json()["total"] == 1
    assert as_user(env, customer_user("c@example.com")).get(f"{M}/audit").status_code == 403


def test_the_old_placeholder_links_stop_once_a_provider_is_set_up(env):
    from app.services.event_service import _auto_meeting_link

    env.providers["zoom"].is_configured = False
    assert _auto_meeting_link("online", "zoom", None).startswith("https://zoom.us/j/")                                  # nothing set up: as before
    env.providers["zoom"].is_configured = True
    assert _auto_meeting_link("online", "zoom", None) is None
    assert _auto_meeting_link("online", "zoom", "https://example.com/mine") == "https://example.com/mine"
