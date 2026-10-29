"""Microsoft Teams, through Microsoft Graph online meetings. Written from Microsoft's public documentation; not run against a live account.

The app registration is multi-tenant. Some organisations need an administrator to approve the connection, and the connecting user needs a
Teams licence (a personal Microsoft account cannot create Teams meetings).
"""
from datetime import datetime, timedelta
from urllib.parse import urlencode

from app.core.config import settings
from app.services.meeting_providers.base import Account, MeetingProvider, MeetingResult, MeetingSpec, ProviderAuthError, ProviderError, Tokens
from app.services.meeting_providers.http import call

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = "offline_access User.Read OnlineMeetings.ReadWrite"


def _graph_time(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")


class TeamsProvider(MeetingProvider):
    key, label = "teams", "Microsoft Teams"

    def __init__(self, client_id: str | None = None, client_secret: str | None = None, tenant: str | None = None, session=None):
        self.client_id = client_id if client_id is not None else settings.MICROSOFT_CLIENT_ID
        self.client_secret = client_secret if client_secret is not None else settings.MICROSOFT_CLIENT_SECRET
        self.tenant = tenant or settings.MICROSOFT_TENANT or "common"
        self.session = session

    @property
    def _base(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0"

    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return f"{self._base}/authorize?" + urlencode({"client_id": self.client_id, "response_type": "code", "redirect_uri": redirect_uri,
                                                         "response_mode": "query", "scope": SCOPES, "state": state})

    def _tokens(self, body: dict, keep_refresh: str | None = None) -> Tokens:
        return Tokens(access_token=body["access_token"], refresh_token=body.get("refresh_token") or keep_refresh,
                      expires_at=datetime.utcnow() + timedelta(seconds=int(body.get("expires_in") or 3600)), scope=body.get("scope"))

    def exchange_code(self, code: str, redirect_uri: str) -> tuple[Tokens, Account]:
        body = call(self.session, "POST", f"{self._base}/token", auth_statuses=(), data={
            "client_id": self.client_id, "client_secret": self.client_secret, "code": code, "redirect_uri": redirect_uri,
            "grant_type": "authorization_code", "scope": SCOPES})
        tokens = self._tokens(body)
        me = call(self.session, "GET", f"{GRAPH}/me", headers=self._headers(tokens))
        return tokens, Account(account_id=me.get("id"), email=me.get("mail") or me.get("userPrincipalName"), name=me.get("displayName"))

    def refresh(self, refresh_token: str) -> Tokens:
        try:
            body = call(self.session, "POST", f"{self._base}/token", auth_statuses=(), data={
                "client_id": self.client_id, "client_secret": self.client_secret, "refresh_token": refresh_token, "grant_type": "refresh_token", "scope": SCOPES})
        except ProviderError as exc:
            if exc.code == "provider_rejected":
                raise ProviderAuthError()
            raise
        return self._tokens(body, keep_refresh=refresh_token)

    @staticmethod
    def _headers(tokens: Tokens) -> dict:
        return {"Authorization": f"Bearer {tokens.access_token}"}

    @staticmethod
    def _body(spec: MeetingSpec) -> dict:
        return {"subject": spec.title[:200], "startDateTime": _graph_time(spec.start_utc), "endDateTime": _graph_time(spec.end_utc)}

    def create(self, tokens: Tokens, spec: MeetingSpec) -> MeetingResult:
        body = call(self.session, "POST", f"{GRAPH}/me/onlineMeetings", headers=self._headers(tokens), json=self._body(spec))
        audio = body.get("audioConferencing") or {}
        dial_in = {"number": audio.get("tollNumber"), "conference_id": audio.get("conferenceId")} if audio.get("tollNumber") else None
        return MeetingResult(external_id=body["id"], join_url=body["joinWebUrl"], dial_in=dial_in)

    def update(self, tokens: Tokens, external_id: str, spec: MeetingSpec) -> None:
        call(self.session, "PATCH", f"{GRAPH}/me/onlineMeetings/{external_id}", headers=self._headers(tokens), json=self._body(spec), expect_json=False)

    def delete(self, tokens: Tokens, external_id: str) -> None:
        try:
            call(self.session, "DELETE", f"{GRAPH}/me/onlineMeetings/{external_id}", headers=self._headers(tokens), expect_json=False)
        except ProviderError as exc:
            if exc.code == "provider_rejected":
                return
            raise
