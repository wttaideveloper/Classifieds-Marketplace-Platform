"""The three meeting providers a business can connect. `get_provider` is the only place the service learns their classes."""
from meetings_service.services.meeting_providers.base import MeetingProvider
from meetings_service.services.meeting_providers.google_meet import GoogleMeetProvider
from meetings_service.services.meeting_providers.teams import TeamsProvider
from meetings_service.services.meeting_providers.zoom import ZoomProvider

PROVIDER_KEYS = ("zoom", "google_meet", "teams")
_CLASSES = {"zoom": ZoomProvider, "google_meet": GoogleMeetProvider, "teams": TeamsProvider}


def get_provider(key: str) -> MeetingProvider:
    try:
        return _CLASSES[key]()
    except KeyError:
        raise ValueError(f"Unknown meeting provider: {key}")


def all_providers() -> list[MeetingProvider]:
    return [get_provider(k) for k in PROVIDER_KEYS]
