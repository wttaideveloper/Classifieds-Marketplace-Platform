"""Zoom (OAuth app + Meetings API). Written from Zoom's public documentation; not run against a live account."""
from datetime import datetime, timedelta
from urllib.parse import urlencode

from app.core.config import settings
from app.services.meeting_providers.base import Account, MeetingProvider, MeetingResult, MeetingSpec, ProviderAuthError, Tokens
from app.services.meeting_providers.http import call

AUTHORIZE_URL = "https://zoom.us/oauth/authorize"
TOKEN_URL = "https://zoom.us/oauth/token"
REVOKE_URL = "https://zoom.us/oauth/revoke"
API = "https://api.zoom.us/v2"


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class ZoomProvider(MeetingProvider):
    key, label = "zoom", "Zoom"

    def __init__(self, client_id: str | None = None, client_secret: str | None = None, session=None):
        self.client_id = client_id if client_id is not None else settings.ZOOM_CLIENT_ID
        self.client_secret = client_secret if client_secret is not None else settings.ZOOM_CLIENT_SECRET
        self.session = session

    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return f"{AUTHORIZE_URL}?" + urlencode({"response_type": "code", "client_id": self.client_id, "redirect_uri": redirect_uri, "state": state})

    def _tokens(self, body: dict) -> Tokens:
        expires = datetime.utcnow() + timedelta(seconds=int(body.get("expires_in") or 3600))
        return Tokens(access_token=body["access_token"], refresh_token=body.get("refresh_token"), expires_at=expires, scope=body.get("scope"))

    def _token_call(self, form: dict) -> dict:
        try:
            return call(self.session, "POST", TOKEN_URL, data=form, auth=(self.client_id, self.client_secret), auth_statuses=())
        except ProviderAuthError:
            raise

    def exchange_code(self, code: str, redirect_uri: str) -> tuple[Tokens, Account]:
        tokens = self._tokens(self._token_call({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}))
        me = call(self.session, "GET", f"{API}/users/me", headers=self._headers(tokens))
        return tokens, Account(account_id=me.get("id"), email=me.get("email"), name=me.get("display_name") or " ".join(filter(None, [me.get("first_name"), me.get("last_name")])) or None)

    def refresh(self, refresh_token: str) -> Tokens:
        try:
            body = self._token_call({"grant_type": "refresh_token", "refresh_token": refresh_token})
        except Exception as exc:
            if getattr(exc, "code", None) == "provider_rejected":      # a revoked or expired refresh token is answered with 400
                raise ProviderAuthError()
            raise
        return self._tokens(body)

    @staticmethod
    def _headers(tokens: Tokens) -> dict:
        return {"Authorization": f"Bearer {tokens.access_token}"}

    @staticmethod
    def _body(spec: MeetingSpec) -> dict:
        return {"topic": spec.title[:200], "type": 2, "start_time": _iso(spec.start_utc), "duration": spec.duration_minutes, "timezone": "UTC",
                "agenda": (spec.description or "")[:2000], "settings": {"join_before_host": True, "waiting_room": False}}

    def create(self, tokens: Tokens, spec: MeetingSpec) -> MeetingResult:
        body = call(self.session, "POST", f"{API}/users/me/meetings", headers=self._headers(tokens), json=self._body(spec))
        return MeetingResult(external_id=str(body["id"]), join_url=body["join_url"], passcode=body.get("password"), host_url=body.get("start_url"),
                             dial_in={"numbers": body.get("settings", {}).get("global_dial_in_numbers")} if body.get("settings", {}).get("global_dial_in_numbers") else None)

    def update(self, tokens: Tokens, external_id: str, spec: MeetingSpec) -> None:
        call(self.session, "PATCH", f"{API}/meetings/{external_id}", headers=self._headers(tokens), json=self._body(spec), expect_json=False)

    def delete(self, tokens: Tokens, external_id: str) -> None:
        try:
            call(self.session, "DELETE", f"{API}/meetings/{external_id}", headers=self._headers(tokens), expect_json=False)
        except Exception as exc:
            if getattr(exc, "code", None) == "provider_rejected":      # already gone at Zoom
                return
            raise

    def revoke(self, tokens: Tokens) -> None:
        try:
            call(self.session, "POST", REVOKE_URL, data={"token": tokens.access_token}, auth=(self.client_id, self.client_secret), expect_json=False, auth_statuses=())
        except Exception:
            pass
