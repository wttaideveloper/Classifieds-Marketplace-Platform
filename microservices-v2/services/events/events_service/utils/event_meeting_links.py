"""The "protected" placeholder that stands in for an Event / session meeting link in API responses.

Event responses never carry a real meeting URL: the Event detail, the list and each session in them show
"protected" instead (see EventResponse / EventSessionResponse). An edit form that loads the Event and sends it back
therefore sends "protected" where the URL was. That value is a placeholder for "unchanged", never a URL to save.
Every write path uses these helpers so the stored link is not overwritten by it.
"""
from __future__ import annotations

MASKED_LINK = "protected"


def is_masked_link(value) -> bool:
    """True for the placeholder (any capitalisation, with or without surrounding spaces)."""
    return isinstance(value, str) and value.strip().lower() == MASKED_LINK


def _identity(session: dict) -> tuple:
    return (str(session.get("session_date") or ""), str(session.get("start_time") or ""), str(session.get("title") or ""))


def keep_stored_session_links(new_sessions: list, stored_sessions: list | None) -> list:
    """The sessions an Event edit is about to save, with each unchanged meeting link carried over from storage.

    A link counts as unchanged when the incoming session either has no `meeting_link` key at all or has the
    "protected" placeholder. The stored session is found by id; a session that has no usable id (legacy rows were
    stored without one, and the edit then gets a fresh id) is found by its date, start time and title, but only when
    exactly one stored session matches. An explicit value is always honoured: a new URL replaces the link, and
    null or an empty string clears it.
    """
    stored = [s for s in (stored_sessions or []) if isinstance(s, dict)]
    by_id = {str(s["id"]): s for s in stored if s.get("id")}
    by_identity: dict[tuple, list[dict]] = {}
    for s in stored:
        by_identity.setdefault(_identity(s), []).append(s)

    result = []
    for session in new_sessions or []:
        if not isinstance(session, dict):
            result.append(session)
            continue
        session = dict(session)
        unchanged = "meeting_link" not in session or is_masked_link(session.get("meeting_link"))
        if unchanged:
            old = by_id.get(str(session.get("id"))) if session.get("id") else None
            if old is None:
                candidates = by_identity.get(_identity(session)) or []
                old = candidates[0] if len(candidates) == 1 and not candidates[0].get("id") else None
            if old is not None and old.get("meeting_link"):
                session["meeting_link"] = old["meeting_link"]
            else:
                session.pop("meeting_link", None)
        result.append(session)
    return result
