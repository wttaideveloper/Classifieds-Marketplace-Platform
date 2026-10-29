"""Google Meet, through a Google Calendar event with a Meet conference. Written from Google's public documentation; not run against a live account.

Needs its own OAuth client (GOOGLE_MEET_CLIENT_ID / SECRET) with the Calendar API enabled. The calendar scopes are "sensitive": Google
verifies the app before more than a few outside accounts can connect.
"""
from datetime import datetime, timedelta
from urllib.parse import urlencode
from uuid import uuid4

from app.core.config import settings
from app.services.meeting_providers.base import Account, MeetingProvider, MeetingResult, MeetingSpec, ProviderAuthError, ProviderError, Tokens
from app.services.meeting_providers.http import call

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
EVENTS = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
SCOPES = "https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/userinfo.profile"


def _rfc3339(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class GoogleMeetProvider(MeetingProvider):
    key, label = "google_meet", "Google Meet"

    def __init__(self, client_id: str | None = None, client_secret: str | None = None, session=None):
        self.client_id = client_id if client_id is not None else settings.GOOGLE_MEET_CLIENT_ID
        self.client_secret = client_secret if client_secret is not None else settings.GOOGLE_MEET_CLIENT_SECRET
        self.session = session

    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        # access_type=offline + prompt=consent so Google returns a refresh token every time the merchant connects
        return f"{AUTHORIZE_URL}?" + urlencode({"client_id": self.client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPES,
                                                "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "state": state})

    def _tokens(self, body: dict, keep_refresh: str | None = None) -> Tokens:
        return Tokens(access_token=body["access_token"], refresh_token=body.get("refresh_token") or keep_refresh,
                      expires_at=datetime.utcnow() + timedelta(seconds=int(body.get("expires_in") or 3600)), scope=body.get("scope"))

    def exchange_code(self, code: str, redirect_uri: str) -> tuple[Tokens, Account]:
        body = call(self.session, "POST", TOKEN_URL, auth_statuses=(), data={
            "code": code, "client_id": self.client_id, "client_secret": self.client_secret, "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
        tokens = self._tokens(body)
        info = call(self.session, "GET", USERINFO_URL, headers=self._headers(tokens))
        return tokens, Account(account_id=info.get("id"), email=info.get("email"), name=info.get("name"))

    def refresh(self, refresh_token: str) -> Tokens:
        try:
            body = call(self.session, "POST", TOKEN_URL, auth_statuses=(), data={
                "refresh_token": refresh_token, "client_id": self.client_id, "client_secret": self.client_secret, "grant_type": "refresh_token"})
        except ProviderError as exc:
            if exc.code == "provider_rejected":               # invalid_grant: the merchant removed our access
                raise ProviderAuthError()
            raise
        return self._tokens(body, keep_refresh=refresh_token)

    @staticmethod
    def _headers(tokens: Tokens) -> dict:
        return {"Authorization": f"Bearer {tokens.access_token}"}

    @staticmethod
    def _event(spec: MeetingSpec, with_conference: bool) -> dict:
        body = {"summary": spec.title[:200], "description": (spec.description or "")[:2000],
                "start": {"dateTime": _rfc3339(spec.start_utc), "timeZone": "UTC"}, "end": {"dateTime": _rfc3339(spec.end_utc), "timeZone": "UTC"}}
        if with_conference:
            body["conferenceData"] = {"createRequest": {"requestId": spec.request_id or uuid4().hex, "conferenceSolutionKey": {"type": "hangoutsMeet"}}}
        return body

    def create(self, tokens: Tokens, spec: MeetingSpec) -> MeetingResult:
        body = call(self.session, "POST", EVENTS, headers=self._headers(tokens), params={"conferenceDataVersion": 1, "sendUpdates": "none"}, json=self._event(spec, True))
        link = body.get("hangoutLink") or next((e.get("uri") for e in (body.get("conferenceData") or {}).get("entryPoints", []) if e.get("entryPointType") == "video"), None)
        if not link:        # Google creates the conference a moment later: read the event once more
            body = call(self.session, "GET", f"{EVENTS}/{body['id']}", headers=self._headers(tokens), params={"conferenceDataVersion": 1})
            link = body.get("hangoutLink")
        if not link:
            raise ProviderError("Google created the event but no Meet link yet. Try again shortly", code="provider_unavailable", retryable=True)
        return MeetingResult(external_id=body["id"], join_url=link)

    def update(self, tokens: Tokens, external_id: str, spec: MeetingSpec) -> None:
        call(self.session, "PATCH", f"{EVENTS}/{external_id}", headers=self._headers(tokens), params={"sendUpdates": "none"}, json=self._event(spec, False))

    def delete(self, tokens: Tokens, external_id: str) -> None:
        try:
            call(self.session, "DELETE", f"{EVENTS}/{external_id}", headers=self._headers(tokens), params={"sendUpdates": "none"}, expect_json=False)
        except ProviderError as exc:
            if exc.code == "provider_rejected":               # 404 / 410: already gone
                return
            raise

    def revoke(self, tokens: Tokens) -> None:
        try:
            call(self.session, "POST", REVOKE_URL, params={"token": tokens.refresh_token or tokens.access_token}, expect_json=False, auth_statuses=())
        except Exception:
            pass
