import logging
import zoneinfo
from datetime import datetime, timezone
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

def _get_utc_now(now=None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)

def _localize_and_convert(dt: datetime | None, event_tz: zoneinfo.ZoneInfo) -> datetime | None:
    if not dt:
        return None
    # Stored timestamps are naive but represent event-local time.
    local_aware = dt.replace(tzinfo=event_tz)
    return local_aware.astimezone(timezone.utc)

def _get_event_tz(event):
    tz_str = getattr(event, "time_zone", None) or "UTC"
    if tz_str == "UTC":
        return timezone.utc
    try:
        return zoneinfo.ZoneInfo(tz_str)
    except Exception as e:
        logger.warning(f"Invalid timezone '{tz_str}' for event, falling back to UTC. Error: {e}")
        return timezone.utc

def validate_registration_window(event, now=None):
    """
    Validates whether the event registration window is open, considering the event's configured timezone.
    Event timestamps (registration_open_at, registration_close_at, registration_cutoff) are stored as
    timezone-naive datetimes but conceptually represent event-local wall-clock time.
    Raises HTTPException if the window is closed.
    """
    current_utc = _get_utc_now(now)
    event_tz = _get_event_tz(event)

    open_at_utc = _localize_and_convert(getattr(event, "registration_open_at", None), event_tz)
    close_at_utc = _localize_and_convert(getattr(event, "registration_close_at", None), event_tz)
    cutoff_utc = _localize_and_convert(getattr(event, "registration_cutoff", None), event_tz)

    if open_at_utc and current_utc <= open_at_utc:
        raise HTTPException(status_code=400, detail=f"Registration not yet open (opens {event.registration_open_at})")
    if close_at_utc and current_utc >= close_at_utc:
        raise HTTPException(status_code=400, detail=f"Registration closed (closed {event.registration_close_at})")
    if cutoff_utc and current_utc >= cutoff_utc:
        raise HTTPException(status_code=400, detail=f"Registration cutoff passed ({event.registration_cutoff})")

def is_registration_open(event, now=None) -> bool:
    """
    Returns True if the event registration window is open, False otherwise.
    """
    try:
        validate_registration_window(event, now)
        return True
    except HTTPException:
        return False
    except Exception:
        return False
