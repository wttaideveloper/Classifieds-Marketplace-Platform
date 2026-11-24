from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ProviderKey = Literal["zoom", "google_meet", "teams"]


class PlatformProvider(BaseModel):
    provider: ProviderKey
    label: str
    enabled: bool = Field(..., description="The Super Admin's switch. When off, nobody can connect it or create new links with it; existing links keep working.")
    configured: bool = Field(..., description="Our app's credentials for this provider are set on the server. A provider that is not configured cannot be connected.")
    connected_businesses: int = 0


class PlatformProviderList(BaseModel):
    items: list[PlatformProvider]


class PlatformProviderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class MerchantProvider(BaseModel):
    provider: ProviderKey
    label: str
    platform_enabled: bool
    configured: bool
    can_connect: bool = Field(..., description="platform_enabled and configured. Show the Connect button only when true.")
    status: Literal["not_connected", "connected", "reconnect_needed"]
    account_email: str | None = None
    account_name: str | None = None
    connected_at: datetime | None = None
    is_default: bool = False


class MerchantProviderList(BaseModel):
    items: list[MerchantProvider]
    default_provider: ProviderKey | None = None
    auto_generate: bool = Field(..., description="The business-wide default for automatic meeting links. Each event can change it.")


class ConnectResponse(BaseModel):
    provider: ProviderKey
    authorization_url: str = Field(..., description="Send the browser here. The provider asks the merchant to sign in and agree, then sends them back to the platform, which sends them on to the app.")
    expires_in_seconds: int


class DisconnectResponse(BaseModel):
    provider: ProviderKey
    status: Literal["not_connected"]


class MerchantSettings(BaseModel):
    default_provider: ProviderKey | None = None
    auto_generate: bool = True


class MerchantSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_provider: ProviderKey | None = Field(None, description="A connected provider, or null to clear.")
    auto_generate: bool | None = None


class EventMeetingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: ProviderKey | None = Field(None, description="The provider for this event. null clears the choice (the business default is used).")
    auto_generate: bool | None = Field(None, description="true: the platform creates the link when the event is published. false: the merchant supplies it.")
    manual_link: str | None = Field(None, max_length=2000, description="A pasted link (https://...). It takes priority over a generated one. null or empty removes it.")


class EventMeeting(BaseModel):
    event_id: str
    virtual: bool = Field(..., description="false for an in-person event: show no meeting settings.")
    provider: ProviderKey | None = None
    provider_label: str | None = None
    auto_generate: bool
    connection_status: Literal["not_connected", "connected", "reconnect_needed"]
    manual_link: str | None = None
    status: Literal["none", "created", "failed", "manual"] = Field(..., description="none = no link yet; created = generated; failed = could not be created (show 'Meeting link not created' with Retry); manual = a pasted link is in use.")
    meeting_link: str | None = Field(None, description="The address customers use: the pasted link if there is one, else the generated one.")
    link_source: Literal["manual", "generated", "none"]
    passcode: str | None = None
    dial_in: dict | None = None
    last_error: str | None = Field(None, description="The provider's reason when status is failed.")
    attempts: int = 0
    needs_attention: bool = False
    generated_at: datetime | None = None


class SessionMeeting(BaseModel):
    """The meeting of one session of an event, or one live session of a training."""
    kind: Literal["event_session", "training_session"]
    resource_id: str = Field(..., description="The event id, or the live session id of a training.")
    session_id: str | None = Field(None, description="The session id (for a session of an event).")
    title: str | None = None
    virtual: bool
    provider: ProviderKey | None = None
    provider_label: str | None = None
    auto_generate: bool
    connection_status: Literal["not_connected", "connected", "reconnect_needed"]
    manual_link: str | None = None
    status: Literal["none", "created", "failed", "manual"]
    meeting_link: str | None = None
    link_source: Literal["manual", "generated", "none"]
    passcode: str | None = None
    dial_in: dict | None = None
    last_error: str | None = None
    attempts: int = 0
    needs_attention: bool = False
    generated_at: datetime | None = None


class SessionMeetingList(BaseModel):
    items: list[SessionMeeting]


class CustomerSession(BaseModel):
    session_id: str | None = None
    title: str | None = None
    link_ready: bool
    meeting_link: str | None = None
    provider: ProviderKey | None = None
    provider_label: str | None = None
    passcode: str | None = None
    dial_in: dict | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    join_opens_at: str | None = None
    can_join_now: bool


class CustomerTrainingMeetings(BaseModel):
    training_id: str
    title: str | None = None
    sessions: list[CustomerSession] = Field(..., description="Every live session, soonest first. `link_ready: false` until a link exists.")


class CustomerMeeting(BaseModel):
    event_id: str
    title: str | None = None
    link_ready: bool = Field(..., description="false until a link exists. Show 'Your meeting link will appear here'.")
    meeting_link: str | None = None
    provider: ProviderKey | None = None
    provider_label: str | None = None
    passcode: str | None = None
    dial_in: dict | None = None
    starts_at: str | None = Field(None, description="UTC instant with a Z.")
    ends_at: str | None = None
    join_opens_at: str | None = Field(None, description="When the Join button becomes active: starts_at minus MEETING_JOIN_OPENS_MINUTES_BEFORE (15). The link itself is returned earlier so it can be copied.")
    can_join_now: bool = Field(..., description="true between join_opens_at and ends_at when a link exists.")
    sessions: list[CustomerSession] = Field(default_factory=list, description="Sessions of the event that have their own link (only those with a link).")


class MeetingAuditItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    action: str
    provider: str | None = None
    actor_id: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    details: dict | None = None
    created_at: datetime


class MeetingAuditList(BaseModel):
    items: list[MeetingAuditItem]
    total: int
