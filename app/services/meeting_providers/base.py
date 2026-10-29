"""What every meeting provider adapter offers, and the shapes the meeting service works with.

The meeting service never talks to Zoom, Google or Microsoft directly: it asks an adapter. The adapters were written from the vendors'
public documentation and have been tested with a fake HTTP session only; run them against real accounts before relying on them.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


class ProviderError(Exception):
    def __init__(self, message: str, code: str = "provider_error", retryable: bool = False, auth: bool = False):
        super().__init__(message)
        self.message, self.code, self.retryable, self.auth = message, code, retryable, auth


class ProviderAuthError(ProviderError):
    """The provider no longer accepts the saved access: the merchant has to reconnect."""

    def __init__(self, message: str = "The provider no longer accepts this connection. Reconnect it"):
        super().__init__(message, code="reconnect_needed", retryable=False, auth=True)


@dataclass
class Tokens:
    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    scope: str | None = None


@dataclass
class Account:
    account_id: str | None = None
    email: str | None = None
    name: str | None = None


@dataclass
class MeetingSpec:
    title: str
    start_utc: datetime              # naive UTC
    end_utc: datetime                # naive UTC
    description: str | None = None
    request_id: str | None = None    # makes a repeated create safe where the provider supports it

    @property
    def duration_minutes(self) -> int:
        return max(1, int((self.end_utc - self.start_utc).total_seconds() // 60))


@dataclass
class MeetingResult:
    external_id: str
    join_url: str
    passcode: str | None = None
    dial_in: dict | None = None
    host_url: str | None = None      # never shown to customers; kept out of the API


@dataclass
class ProviderInfo:
    key: str
    label: str
    client_id: str = ""
    extra: dict = field(default_factory=dict)


class MeetingProvider(ABC):
    key: str = ""
    label: str = ""

    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    def authorize_url(self, state: str, redirect_uri: str) -> str: ...

    @abstractmethod
    def exchange_code(self, code: str, redirect_uri: str) -> tuple[Tokens, Account]: ...

    @abstractmethod
    def refresh(self, refresh_token: str) -> Tokens: ...

    @abstractmethod
    def create(self, tokens: Tokens, spec: MeetingSpec) -> MeetingResult: ...

    @abstractmethod
    def update(self, tokens: Tokens, external_id: str, spec: MeetingSpec) -> None: ...

    @abstractmethod
    def delete(self, tokens: Tokens, external_id: str) -> None: ...

    def revoke(self, tokens: Tokens) -> None:
        """Best effort. A provider with no revoke call does nothing: the tokens are deleted on our side either way."""
        return None
