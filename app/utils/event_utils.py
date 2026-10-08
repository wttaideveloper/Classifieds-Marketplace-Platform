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

def get_event_timezone(event):
    """The event's configured IANA timezone (``Event.time_zone``, e.g. "Asia/Kolkata"), or UTC if unset/invalid.
    The single canonical resolver for "which timezone does this event's organizer-entered wall-clock time mean" —
    reused by registration-window validation (below) and by Phase 2.8 meal/accommodation purchase/service windows
    (app/utils/event_meals.py, app/utils/event_accommodation.py). Do not re-derive this elsewhere.
    """
    tz_str = getattr(event, "time_zone", None) or "UTC"
    if tz_str == "UTC":
        return timezone.utc
    try:
        return zoneinfo.ZoneInfo(tz_str)
    except Exception as e:
        logger.warning(f"Invalid timezone '{tz_str}' for event, falling back to UTC. Error: {e}")
        return timezone.utc


def resolve_naive_or_aware(dt: datetime | None, event_tz) -> datetime | None:
    """A datetime that may be naive OR already timezone-aware -> aware UTC.

    A naive value (no tzinfo) is the organizer's own event-local wall-clock entry (e.g. a
    datetime-local picker with no offset) and is localized to ``event_tz`` before converting to
    UTC. An already-aware value (explicit offset or Z) is trusted exactly as given and only
    converted to UTC for comparison — never re-localized, so pre-existing tz-aware data keeps
    working unchanged. Unlike ``_localize_and_convert`` (which assumes every input is naive), use
    this wherever a field's timezone-awareness may vary depending on when/how it was entered.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=event_tz).astimezone(timezone.utc)
    return dt.astimezone(timezone.utc)

def validate_registration_window(event, now=None):
    """
    Validates whether the event registration window is open, considering the event's configured timezone.
    Event timestamps (registration_open_at, registration_close_at, registration_cutoff) are stored as
    timezone-naive datetimes but conceptually represent event-local wall-clock time.
    Raises HTTPException if the window is closed.
    """
    current_utc = _get_utc_now(now)
    event_tz = get_event_timezone(event)

    # These fields were historically stored without an offset, but API clients may
    # legitimately send an ISO timestamp with one.  Preserve explicit offsets and
    # only localize organizer-entered wall-clock values.
    open_at_utc = resolve_naive_or_aware(getattr(event, "registration_open_at", None), event_tz)
    close_at_utc = resolve_naive_or_aware(getattr(event, "registration_close_at", None), event_tz)
    cutoff_utc = resolve_naive_or_aware(getattr(event, "registration_cutoff", None), event_tz)

    if open_at_utc and current_utc <= open_at_utc:
        raise HTTPException(status_code=400, detail=f"Registration not yet open (opens {event.registration_open_at})")
    if close_at_utc and current_utc >= close_at_utc:
        raise HTTPException(status_code=400, detail=f"Registration closed (closed {event.registration_close_at})")
    if cutoff_utc and current_utc >= cutoff_utc:
        raise HTTPException(status_code=400, detail=f"Registration cutoff passed ({event.registration_cutoff})")

    # A published status is not an assertion that the event is still in the
    # future.  Do this after explicit registration-window messages so existing
    # organizer controls retain their more specific response, but before any new
    # registration, quote, checkout, or waitlist mutation can proceed.
    if get_event_lifecycle_state(event, current_utc) == "finished":
        raise HTTPException(status_code=400, detail="Event has already ended and registration is closed.")

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

def get_event_lifecycle_state(event, now=None) -> str | None:
    """
    Calculates the lifecycle state of the event based on its dates and timezone.
    Returns: 'upcoming', 'ongoing', 'finished', or None if no valid start_date exists.
    """
    start_date = getattr(event, "start_date", None)
    if not start_date:
        return None
    
    current_utc = _get_utc_now(now)
    event_tz = get_event_timezone(event)
    
    start_utc = resolve_naive_or_aware(start_date, event_tz)
    end_date = getattr(event, "end_date", None)
    end_utc = resolve_naive_or_aware(end_date, event_tz) if end_date else start_utc
        
    if start_utc is None:
        return None
        
    if current_utc < start_utc:
        return "upcoming"
    elif current_utc > end_utc:
        return "finished"
    else:
        return "ongoing"
