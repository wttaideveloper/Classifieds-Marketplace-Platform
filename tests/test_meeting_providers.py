"""The Zoom, Google Meet and Teams adapters, against a fake HTTP session: the requests they build and how they read answers and errors.

Written from the vendors' public documentation. Nothing here talks to a real provider, so it proves the shapes, not that the vendors accept them.
"""
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from app.services.meeting_providers.base import MeetingSpec, ProviderAuthError, ProviderError, Tokens
from app.services.meeting_providers.google_meet import GoogleMeetProvider
from app.services.meeting_providers.teams import TeamsProvider
from app.services.meeting_providers.zoom import ZoomProvider

SPEC = MeetingSpec(title="Online Yoga", start_utc=datetime(2026, 11, 20, 7, 0), end_utc=datetime(2026, 11, 20, 8, 30), description="Bring a mat")
TOKENS = Tokens("acc-1", "ref-1", datetime(2030, 1, 1))


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body
        self.content = b"x" if body is not None else b""

    def json(self):
        if self._body is None:
            raise ValueError
        return self._body


class Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# ---------------------------------------------------------------- Zoom

def test_zoom_authorize_url_and_code_exchange():
    session = Session(Resp(200, {"access_token": "a", "refresh_token": "r", "expires_in": 3600, "scope": "meeting:write"}),
                      Resp(200, {"id": "u1", "email": "host@example.com", "display_name": "Host"}))
    zoom = ZoomProvider("cid", "secret", session)

    query = parse_qs(urlparse(zoom.authorize_url("STATE", "https://api.example.com/cb")).query)
    tokens, account = zoom.exchange_code("CODE", "https://api.example.com/cb")

    assert urlparse(zoom.authorize_url("s", "r")).netloc == "zoom.us" and query["client_id"] == ["cid"] and query["state"] == ["STATE"] and query["response_type"] == ["code"]
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("POST", "https://zoom.us/oauth/token") and kwargs["auth"] == ("cid", "secret") and kwargs["data"]["grant_type"] == "authorization_code"
    assert session.calls[1][2]["headers"] == {"Authorization": "Bearer a"}
    assert (tokens.access_token, tokens.refresh_token) == ("a", "r") and tokens.expires_at > datetime.utcnow() and (account.email, account.name) == ("host@example.com", "Host")


def test_zoom_creates_updates_and_deletes_a_meeting():
    session = Session(Resp(201, {"id": 123456789, "join_url": "https://zoom.us/j/123456789?pwd=x", "password": "abc", "start_url": "https://zoom.us/s/host"}), Resp(204), Resp(204),
                      Resp(404, {"code": 3001, "message": "Meeting does not exist"}))
    zoom = ZoomProvider("cid", "secret", session)

    made = zoom.create(TOKENS, SPEC)
    zoom.update(TOKENS, "123456789", SPEC)
    zoom.delete(TOKENS, "123456789")
    zoom.delete(TOKENS, "123456789")                                                           # already gone: not an error

    assert made.external_id == "123456789" and made.join_url.startswith("https://zoom.us/j/") and made.passcode == "abc" and made.host_url == "https://zoom.us/s/host"
    body = session.calls[0][2]["json"]
    assert session.calls[0][1] == "https://api.zoom.us/v2/users/me/meetings" and body["start_time"] == "2026-11-20T07:00:00Z" and body["duration"] == 90 and body["topic"] == "Online Yoga"
    assert (session.calls[1][0], session.calls[1][1]) == ("PATCH", "https://api.zoom.us/v2/meetings/123456789")
    assert session.calls[2][0] == "DELETE"


def test_zoom_refresh_and_a_revoked_refresh_token():
    assert ZoomProvider("c", "s", Session(Resp(200, {"access_token": "new", "refresh_token": "r2", "expires_in": 3600}))).refresh("r").access_token == "new"
    with pytest.raises(ProviderAuthError):
        ZoomProvider("c", "s", Session(Resp(400, {"reason": "Invalid refresh token"}))).refresh("r")


# ---------------------------------------------------------------- Google Meet

def test_google_authorize_url_asks_for_offline_calendar_access():
    google = GoogleMeetProvider("gid", "gsecret", Session())

    query = parse_qs(urlparse(google.authorize_url("STATE", "https://api.example.com/cb")).query)

    assert query["access_type"] == ["offline"] and query["prompt"] == ["consent"] and "calendar.events" in query["scope"][0] and query["state"] == ["STATE"]
    assert urlparse(google.authorize_url("s", "r")).netloc == "accounts.google.com"


def test_google_creates_a_calendar_event_with_a_meet_link():
    session = Session(Resp(200, {"id": "evt1", "hangoutLink": "https://meet.google.com/abc-defg-hij"}), Resp(200, {}), Resp(204), Resp(410, {"error": {"message": "Resource has been deleted"}}))
    google = GoogleMeetProvider("gid", "gsecret", session)

    made = google.create(TOKENS, SPEC)
    google.update(TOKENS, "evt1", SPEC)
    google.delete(TOKENS, "evt1")
    google.delete(TOKENS, "evt1")

    assert made.external_id == "evt1" and made.join_url == "https://meet.google.com/abc-defg-hij"
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("POST", "https://www.googleapis.com/calendar/v3/calendars/primary/events") and kwargs["params"]["conferenceDataVersion"] == 1
    assert kwargs["json"]["conferenceData"]["createRequest"]["conferenceSolutionKey"] == {"type": "hangoutsMeet"} and kwargs["json"]["start"]["dateTime"] == "2026-11-20T07:00:00Z"
    assert "conferenceData" not in session.calls[1][2]["json"]                                    # an update keeps the same Meet room


def test_google_reads_the_event_again_when_the_meet_link_is_not_ready_yet():
    session = Session(Resp(200, {"id": "evt1", "conferenceData": {"createRequest": {"status": {"statusCode": "pending"}}}}), Resp(200, {"id": "evt1", "hangoutLink": "https://meet.google.com/x"}))

    assert GoogleMeetProvider("g", "s", session).create(TOKENS, SPEC).join_url == "https://meet.google.com/x"
    still = Session(Resp(200, {"id": "e"}), Resp(200, {"id": "e"}))
    with pytest.raises(ProviderError) as info:
        GoogleMeetProvider("g", "s", still).create(TOKENS, SPEC)
    assert info.value.retryable is True


def test_google_keeps_the_refresh_token_it_already_has_and_maps_invalid_grant():
    refreshed = GoogleMeetProvider("g", "s", Session(Resp(200, {"access_token": "n", "expires_in": 3600}))).refresh("keep-me")
    assert refreshed.access_token == "n" and refreshed.refresh_token == "keep-me"
    with pytest.raises(ProviderAuthError):
        GoogleMeetProvider("g", "s", Session(Resp(400, {"error": "invalid_grant", "error_description": "Token has been revoked"}))).refresh("r")


# ---------------------------------------------------------------- Teams

def test_teams_authorize_url_and_online_meeting():
    session = Session(Resp(201, {"id": "mtg1", "joinWebUrl": "https://teams.microsoft.com/l/meetup-join/abc",
                                 "audioConferencing": {"tollNumber": "+1 555 0100", "conferenceId": "98765"}}), Resp(200, {}), Resp(204))
    teams = TeamsProvider("mid", "msecret", "common", session)

    query = parse_qs(urlparse(teams.authorize_url("STATE", "https://api.example.com/cb")).query)
    made = teams.create(TOKENS, SPEC)
    teams.update(TOKENS, "mtg1", SPEC)
    teams.delete(TOKENS, "mtg1")

    assert urlparse(teams.authorize_url("s", "r")).netloc == "login.microsoftonline.com" and "OnlineMeetings.ReadWrite" in query["scope"][0] and "offline_access" in query["scope"][0]
    assert made.external_id == "mtg1" and made.join_url.startswith("https://teams.microsoft.com/") and made.dial_in == {"number": "+1 555 0100", "conference_id": "98765"}
    body = session.calls[0][2]["json"]
    assert session.calls[0][1] == "https://graph.microsoft.com/v1.0/me/onlineMeetings" and body["subject"] == "Online Yoga" and body["startDateTime"].startswith("2026-11-20T07:00:00")
    assert session.calls[1][0] == "PATCH" and session.calls[2][0] == "DELETE"


def test_teams_account_comes_from_graph_me():
    session = Session(Resp(200, {"access_token": "a", "refresh_token": "r", "expires_in": 3600}), Resp(200, {"id": "u", "mail": None, "userPrincipalName": "host@contoso.com", "displayName": "Host"}))

    _, account = TeamsProvider("m", "s", "common", session).exchange_code("c", "https://api.example.com/cb")

    assert (account.email, account.name) == ("host@contoso.com", "Host")


# ---------------------------------------------------------------- failures, for every provider

@pytest.mark.parametrize("provider", [ZoomProvider, GoogleMeetProvider, TeamsProvider])
def test_failures_are_mapped_the_same_way(provider):
    def make(response):
        return provider("id", "secret", session=Session(response))

    with pytest.raises(ProviderAuthError):
        make(Resp(401, {"message": "Invalid access token"})).create(TOKENS, SPEC)
    with pytest.raises(ProviderError) as busy:
        make(Resp(429, {})).create(TOKENS, SPEC)
    assert busy.value.retryable is True and busy.value.code == "provider_unavailable"
    with pytest.raises(ProviderError) as down:
        make(Resp(503, {})).create(TOKENS, SPEC)
    assert down.value.retryable is True
    with pytest.raises(ProviderError) as limit:
        make(Resp(400, {"message": "You have reached the meeting limit of your plan"})).create(TOKENS, SPEC)
    assert limit.value.retryable is False and "meeting limit" in limit.value.message
    with pytest.raises(ProviderError) as slow:
        make(requests.Timeout()).create(TOKENS, SPEC)
    assert slow.value.code == "provider_timeout" and slow.value.retryable is True
    with pytest.raises(ProviderError) as gone:
        make(requests.ConnectionError()).create(TOKENS, SPEC)
    assert gone.value.code == "network_error"


@pytest.mark.parametrize("provider,configured", [(ZoomProvider, "zoom"), (GoogleMeetProvider, "google"), (TeamsProvider, "teams")])
def test_a_provider_without_credentials_is_not_configured(provider, configured):
    assert provider("", "", session=Session()).configured() is False
    assert provider("id", "secret", session=Session()).configured() is True


def test_an_error_message_never_contains_a_token():
    with pytest.raises(ProviderError) as info:
        ZoomProvider("c", "s", Session(Resp(400, {"message": "Bad request"}))).create(Tokens("SUPER-SECRET-TOKEN"), SPEC)
    assert "SUPER-SECRET-TOKEN" not in info.value.message
