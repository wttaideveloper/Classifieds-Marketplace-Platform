# Event and session meeting links

## What "protected" is

`"protected"` is not an error and not the saved value. It is a deliberate redaction. Every Event response (the Event details, the list, and the `sessions` inside them) shows `"protected"` in place of a meeting URL, for everyone, including the Enterprise Admin who owns the event. A real meeting URL is only ever returned by the endpoints below, which check who is asking.

## Reading the saved links (owner)

`GET /api/v1/events/{event_id}/meeting-links` returns the Event link and every session link as saved, in one call. Use it to fill the edit form.

```json
{
  "event_id": "…",
  "delivery_mode": "hybrid",
  "meeting_provider": "zoom",
  "meeting_link": "https://zoom.us/j/1234567890",
  "sessions": [
    { "id": "s1", "title": "Day 1", "session_date": "2026-10-20", "start_time": "09:00", "meeting_link": "https://meet.example.com/day-1" }
  ]
}
```

Who can call it: the Enterprise Admin or a provider of the business that owns the event, and a Super Admin. Anyone else gets 403 (401 without a login). Registered attendees do not use this endpoint; they keep using the existing ones below.

The older endpoints still work and are unchanged:
- `GET /events/{id}/meeting-link`: the Event link, for owning staff and registered participants.
- `GET /events/{id}/sessions/{session_id}/meeting-link`: one session's link, same rule.
- `GET /events/{id}/sessions`: owning staff get the stored sessions with real links; everyone else gets `"protected"`.

## Saving

Whatever the edit form sends, a stored link is only changed when you send a real value.

| You send | Result |
|---|---|
| a URL | the link is replaced |
| `null` or `""` (sessions) | the link is cleared |
| `"protected"` | unchanged |
| nothing for that field | unchanged |

This applies to the Event link and to each session in `PUT /events/{id}`, and to `PUT /events/{id}/sessions/{session_id}`. In `PUT /events/{id}` the `sessions` list replaces the stored list, but each session that sends `"protected"` or omits `meeting_link` keeps its own stored link. A session is matched to the stored one by `id`. Old sessions that were saved without an id are matched by date, start time and title, and only when exactly one stored session matches; if two are identical the link is not carried over (it is better to lose a link than to attach the wrong one).

A new session (an id that is not stored) never receives another session's link. `POST /events/{id}/sessions` and `POST /events` with `"protected"` as the link save no link at all; the word is never stored.

Unrelated edits (a title, a price) that do not send the links, or send them back as `"protected"`, leave them alone. The answer to the update is still masked.

## Not changed

Event Details, the list, and the session responses still show `"protected"` to everyone. No public or customer response gained a real URL.
