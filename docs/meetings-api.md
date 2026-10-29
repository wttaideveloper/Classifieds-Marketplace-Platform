# Virtual meeting providers API (Zoom, Google Meet, Microsoft Teams)

For the web and mobile developers. Base path `/api/v1/meetings`. All times are UTC (text ending in `Z`).

The platform only **creates and shares the meeting link**. It does not host the call, record it or track who joined. The meeting happens in the provider's own app or website.

**What works today:** online and hybrid **events** (one meeting for the event, and optionally one meeting per session of the event) and the **live sessions of trainings** (one meeting each). Each can use a generated or a pasted link, and registered or enrolled customers get it. **Not built:** bookings of services (there is no booking system yet), programs (they have no meeting fields), and reusing a calendar connection (there is no calendar sync yet). See section 9.

**Tested with:** a fake provider, and the three adapters against a fake HTTP session. **Not tested against real Zoom, Google or Microsoft accounts.** The adapters were written from the vendors' public documentation. Do a full run with each provider (connect, create, reschedule, cancel, disconnect, expired token) before release.

## 1. Who can do what

| Action | Super Admin | Enterprise Admin | Provider (staff) | Customer |
|---|---|---|---|---|
| Switch a provider on or off for the platform | yes | no | no | no |
| Connect, disconnect, choose the default, business settings | no | yes | no | no |
| An event's, session's or training session's meeting: read | yes | own business | own business | no |
| The same: choose provider, paste a link, generate, delete | no (403) | own business | own business | no |
| Open the meeting of an event they registered for, or a training they are enrolled in | n/a | n/a | n/a | yes, own registration or enrolment |
| Audit | everything | own business | no | no |

An event of another business is a plain 404, like the event detail. A training of another business is 403, like the other training management endpoints.

## 2. How it works

1. The Super Admin decides which providers the platform offers (all three are on by default).
2. A business's Enterprise Admin connects a provider (section 4). Connecting only makes it available.
3. For an online or hybrid event the merchant chooses a provider, or relies on the business default, and leaves **automatic links** on or off.
4. **Automatic:** when the event is published (or the merchant presses Generate) the platform creates the meeting at the provider with the event's title, start and end, and saves the link on the event. If the event is rescheduled the meeting is moved and the link stays the same. If the event is cancelled, withdrawn from published, or deleted, the meeting is deleted at the provider.
5. **Manual:** the merchant can paste any link. A pasted link always wins over a generated one. A link already typed into the event form counts as pasted.
6. If creating the meeting fails the event is not affected. The failure is recorded, retried a few times in the background (every 10 minutes, up to 5 attempts), and shown on the event with a Retry button and the option to paste a link.
7. Customers registered for the event open **My meeting** (section 6). The link is also saved as the event's own `meeting_link`, so the existing endpoints (`GET /events/{id}/meeting-link`) and reminder emails use it unchanged.

## 3. Platform switches (Super Admin)

`GET /meetings/admin/providers`
```json
{ "items": [ { "provider": "zoom", "label": "Zoom", "enabled": true, "configured": true, "connected_businesses": 3 },
             { "provider": "google_meet", "label": "Google Meet", "enabled": true, "configured": false, "connected_businesses": 0 },
             { "provider": "teams", "label": "Microsoft Teams", "enabled": true, "configured": true, "connected_businesses": 1 } ] }
```
`configured` means the platform's own app credentials for that provider are set on the server (section 8).

`PUT /meetings/admin/providers/{provider}` with `{ "enabled": false }` switches one off. Nobody can connect it or create new links with it. **Links that already exist keep working.** Businesses that had it connected are notified (`meeting_provider_disabled`).

## 4. Connecting a provider (Enterprise Admin)

`GET /meetings/providers`
```json
{
  "items": [
    { "provider": "zoom", "label": "Zoom", "platform_enabled": true, "configured": true, "can_connect": true, "status": "connected",
      "account_email": "host@example.com", "account_name": "Host Person", "connected_at": "2026-10-28T10:00:00", "is_default": true },
    { "provider": "google_meet", "label": "Google Meet", "platform_enabled": true, "configured": true, "can_connect": true, "status": "not_connected",
      "account_email": null, "account_name": null, "connected_at": null, "is_default": false },
    { "provider": "teams", "label": "Microsoft Teams", "platform_enabled": false, "configured": true, "can_connect": false, "status": "not_connected",
      "account_email": null, "account_name": null, "connected_at": null, "is_default": false }
  ],
  "default_provider": "zoom",
  "auto_generate": true
}
```
`status` is `not_connected`, `connected` or `reconnect_needed`. Show the Connect button only when `can_connect` is true. A business can connect all three at once, one account for each.

**Connect**

1. `POST /meetings/providers/{provider}/connect` returns `{ "provider": "zoom", "authorization_url": "https://zoom.us/oauth/authorize?...", "expires_in_seconds": 600 }`. 409 when the platform switched the provider off or has not set it up.
2. Redirect the browser to `authorization_url`. The merchant signs in at the provider and agrees. The platform never sees their password.
3. The provider sends them to `GET /api/v1/meetings/oauth/callback/{provider}` (called by the provider, not by the app). It stores the connection and redirects to
   `{FRONTEND_URL}/settings/meetings?meeting_provider=zoom&meeting_connected=true`
   or `...&meeting_connected=false&reason=denied` (also `invalid_state`, `provider_unavailable`, `provider_rejected`, `provider_timeout`, `network_error`).
4. The app then calls `GET /meetings/providers` to show the account name.

The `state` of the redirect is signed and expires after 10 minutes, so a forged or old callback is refused. Tokens are stored encrypted. They are never returned by an API, written to the logs or put in a notification.

**Disconnect:** `DELETE /meetings/providers/{provider}` deletes the stored tokens and revokes access at the provider where it allows that (Zoom and Google do). Links already created keep their address. Safe to call twice.

**Reconnect needed:** if the provider says our access was withdrawn or expired, the status becomes `reconnect_needed` and the merchant is notified (`meeting_reconnect_needed`). Creating links with that provider fails with a plain message until they connect again.

**Business settings**

`GET /meetings/settings` and `PUT /meetings/settings` (send only what changes):
```json
{ "default_provider": "zoom", "auto_generate": true }
```
The default provider must be connected (409 otherwise); `null` clears it. `auto_generate` is the business-wide default for new events.

## 5. An event's meeting (the business)

`GET /meetings/events/{event_id}`
```json
{
  "event_id": "b0e2…", "virtual": true, "provider": "zoom", "provider_label": "Zoom", "auto_generate": true, "connection_status": "connected",
  "manual_link": null, "status": "created", "meeting_link": "https://zoom.us/j/123456789?pwd=…", "link_source": "generated",
  "passcode": "123456", "dial_in": { "number": "+1 555 0100" }, "last_error": null, "attempts": 0, "needs_attention": false, "generated_at": "2026-10-28T10:05:00"
}
```

| `status` | Show |
|---|---|
| `none` | no link yet |
| `created` | the generated link |
| `failed` | "Meeting link not created", `last_error`, a **Retry** button and a field to paste a link (`needs_attention: true`) |
| `manual` | the pasted link |

`provider` falls back to the business default when the event has none. `virtual: false` is an in-person event: show no meeting settings.

| Call | What it does |
|---|---|
| `PUT /meetings/events/{id}` | send only what changes: `provider` (`zoom` / `google_meet` / `teams`, or `null` for the business default), `auto_generate` (true / false), `manual_link` (an `https://` address; `null` or `""` removes it). Customers registered for the event are told when the link changes. An in-person event is 422 |
| `POST /meetings/events/{id}/generate` | create the meeting now. This is also the Retry button. 409 with a plain reason when no provider is chosen ("Choose a meeting provider for this event, or paste a link"), it is not connected ("Connect Zoom first"), it needs reconnecting, or it is switched off. A provider error is **not** an error response: the answer has `status: "failed"` and `last_error`. Does nothing when a pasted link is in use or the meeting already exists |
| `DELETE /meetings/events/{id}/generated` | delete the meeting at the provider and remove the generated link. A pasted link is left alone |

`GET /meetings/audit?limit=50` (and `tenant_id=` for a Super Admin) lists who connected, disconnected or changed a link, newest first. It never holds a token.

## 5a. One session of an event

An event can have one meeting for the whole event (section 5) and, separately, one meeting per session. A session has its own meeting only when the merchant chose a provider or pasted a link **for that session**. The link is kept on the session (`sessions[].meeting_link`), so the existing session endpoints and emails use it.

| Call | What it does |
|---|---|
| `GET /meetings/events/{event_id}/sessions` | every session with its meeting state: `{ "items": [ { "kind": "event_session", "resource_id": "<event id>", "session_id": "s1", "title": "Day 1", "status": "none", … } ] }` |
| `GET /meetings/events/{event_id}/sessions/{session_id}` | one session's state (same fields as the event's meeting) |
| `PUT /meetings/events/{event_id}/sessions/{session_id}` | `provider`, `auto_generate`, `manual_link`: same rules as the event |
| `POST /meetings/events/{event_id}/sessions/{session_id}/generate` | create it now, also Retry. 422 when the session has no date and start time. The end defaults to one hour after the start |
| `DELETE /meetings/events/{event_id}/sessions/{session_id}/generated` | delete the generated meeting |

Times come from the session's `session_date`, `start_time` and `end_time`, read in the event's `time_zone`. When the event is published, a session that has a provider chosen (or the business default is used) and automatic links on gets its meeting too. Editing a session's date or time moves its meeting. Deleting the session deletes its meeting. Cancelling or withdrawing the event deletes the event's meeting and every session's meeting.

## 5b. Live sessions of a training

Each live session (`POST /trainings/{id}/live-sessions`) can have its own meeting. `meeting_link` is **now optional** when creating a live session: leave it out to have the platform create the meeting, or send a link as before (it is then treated as pasted and nothing is generated).

| Call | What it does |
|---|---|
| `GET /meetings/trainings/{training_id}/sessions` | every live session with its meeting state, soonest first |
| `GET /meetings/trainings/{training_id}/sessions/{session_id}` | one live session's state. `session_id` is the live session's id |
| `PUT /meetings/trainings/{training_id}/sessions/{session_id}` | `provider`, `auto_generate`, `manual_link` |
| `POST /meetings/trainings/{training_id}/sessions/{session_id}/generate` | create it now, also Retry |
| `DELETE /meetings/trainings/{training_id}/sessions/{session_id}/generated` | delete the generated meeting |

Who: the training's Enterprise Admin or Provider (403 for another business's training), and a Super Admin to read.

- The meeting is created when the training is **published** (or when a live session is created on an already published training), using the session's `scheduled_at` (read in the training's `time_zone`) and `duration_minutes`. The default provider is used unless the merchant chose one for the session.
- The link and provider are also written to the live session's own `meeting_link` and `meeting_provider` (`zoom`, `meet` or `teams`), so `GET /trainings/{id}/meeting-link`, the calendar export and the existing screens already show it.
- When the training is unpublished, suspended, cancelled, archived or deleted, the generated meetings are deleted at the provider. A pasted link is kept.
- A live session has no edit endpoint today, so rescheduling a training session is not wired. Delete the generated meeting and generate again after changing it by other means.

## 6. Customers

`GET /meetings/my/events/{event_id}`
```json
{
  "event_id": "b0e2…", "title": "Online Yoga", "link_ready": true, "meeting_link": "https://zoom.us/j/123456789?pwd=…",
  "provider": "zoom", "provider_label": "Zoom", "passcode": "123456", "dial_in": { "number": "+1 555 0100" },
  "starts_at": "2026-11-20T07:00:00Z", "ends_at": "2026-11-20T08:30:00Z", "join_opens_at": "2026-11-20T06:45:00Z", "can_join_now": false
}
```
- Only for the caller's own **confirmed or attended** registration of a **published or completed** event. Anyone else (not registered, pending, cancelled, a cancelled or draft event, someone else's event) gets 404.
- Before a link exists: `link_ready: false`, `meeting_link: null`. Show "Your meeting link will appear here". The customer is notified when it is ready (`meeting_link_added`) and when it changes (`meeting_link_changed`).
- The link is returned as soon as it exists so it can be copied. `can_join_now` is true from `join_opens_at` (15 minutes before the start, `MEETING_JOIN_OPENS_MINUTES_BEFORE`) until `ends_at`. Enable the Join button on it. The customer needs no account with the provider.
- A host link (to start the meeting) is never returned to anyone.
- `sessions` lists the event's sessions that have their own link (same fields as below, plus `session_id` and `title`). It is empty when none has.

### A training's live sessions

`GET /meetings/my/trainings/{training_id}`
```json
{
  "training_id": "a64f…", "title": "Breathwork",
  "sessions": [
    { "session_id": "5d1c…", "title": "Week 1 call", "link_ready": true, "meeting_link": "https://zoom.us/j/123456789?pwd=…", "provider": "zoom", "provider_label": "Zoom",
      "passcode": "123456", "dial_in": null, "starts_at": "2026-11-20T09:30:00Z", "ends_at": "2026-11-20T10:30:00Z", "join_opens_at": "2026-11-20T09:15:00Z", "can_join_now": false }
  ]
}
```
For a learner with an active (`enrolled`, `active`, `approved`, `completed` or `attended`), unexpired enrolment in a published or completed training. Anyone else (not enrolled, pending, expired access, a draft training) gets 404. Soonest session first. `link_ready: false` until a link exists. The same join window as events.

## 7. Notifications

Through the existing notification feed with push. They are sent after the change is saved and a failure never breaks the call. They carry no link: the customer opens the event.

| Category | To | When |
|---|---|---|
| `meeting_link_added` | registered customers | the link was created |
| `meeting_link_changed` | registered customers | a pasted link was added, changed or removed |
| `meeting_link_failed` | the merchant who acted / connected the account | creating the meeting failed (first failure, and when the attempts run out) |
| `meeting_reconnect_needed` | the merchant who connected the account | the provider no longer accepts the connection |
| `meeting_provider_disabled` | merchants who had it connected | the Super Admin switched it off |

## 8. Setting up (deployment)

Each provider needs the platform's own app registered once. This takes calendar time because Zoom and Google review apps before outside accounts can connect (see the dependency document).

| Setting | Meaning |
|---|---|
| `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET` | the Zoom Marketplace OAuth app |
| `GOOGLE_MEET_CLIENT_ID`, `GOOGLE_MEET_CLIENT_SECRET` | a Google OAuth client with the Calendar API enabled. Not the sign-in client |
| `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_TENANT` | the Entra app (multi-tenant, `common` by default) with `OnlineMeetings.ReadWrite`, `User.Read`, `offline_access` |
| `MEETING_TOKEN_ENCRYPTION_KEY` | key that encrypts stored tokens (any long random text). Set it, or rotating `SECRET_KEY` makes every connection unreadable |
| `MEETING_OAUTH_REDIRECT_BASE` | public base of this API (else `PUBLIC_API_BASE_URL`, else the request's address) |
| `FRONTEND_URL` | where the merchant lands after connecting: `{FRONTEND_URL}/settings/meetings` |
| `MEETING_JOIN_OPENS_MINUTES_BEFORE` | default 15 |
| `MEETING_GENERATION_MAX_ATTEMPTS`, `MEETING_PROVIDER_TIMEOUT_SECONDS` | 5 and 20 |
| `MEETING_JOBS_ENABLED`, `MEETING_JOBS_INTERVAL_SECONDS` | the background retry (on in production by default, every 600 seconds) |

**Redirect addresses to register at each provider:** `https://<api>/api/v1/meetings/oauth/callback/zoom`, `.../google_meet` and `.../teams`.

Migration: `f3b8d1a6c2e4` (run `alembic upgrade heads`). It creates `meeting_provider_settings`, `meeting_connections`, `meeting_merchant_settings`, `meeting_links` (one row per event, event session or training live session) and `meeting_audit`.

## 9. Decisions taken, and what is not built

The feature list left seven decisions open. These are in place now:

1. **Calendar connection:** Google Meet and Teams use their own connection, separate from any calendar sync. Reusing one sign-in needs the calendar feature, which does not exist yet.
2. **Several sessions:** an event has one meeting for the whole event, and a session can additionally have its own (section 5a). A training's live sessions each have their own.
3. **Join button:** active from 15 minutes before the start until the end. The link itself is returned earlier so it can be copied.
4. **Manual link:** always wins over a generated one.
5. **Platform switch:** the Super Admin can switch a provider off for everyone.
6. **No provider and no link:** the event can still be published. The merchant sees `status: none` and can connect a provider or paste a link. Publishing is not blocked.
7. **One account per provider** for each business.

Other things to know:
- **Older placeholder links.** The event code used to invent fake links (a random Zoom id) when an online event had a provider but no link. Once a provider's app is configured on the server, that stops: links come only from the real meeting service. While a provider is not configured the old behaviour is unchanged.
- **Copying an event** does not copy a generated link (the copy would share the original's meeting). A pasted link is copied as before.
- **Not built:** service bookings (no booking system yet), programs (no meeting fields exist), calendar invites and sync, recordings, attendance, advanced host settings, and more than one account per provider. A training's live session cannot be rescheduled through the meeting module because there is no edit endpoint for it.
- **Notifications** for a training go to its enrolled learners, and for an event or session to its registered customers (`meeting_link_added`, `meeting_link_changed`).
- **Provider limits** (meeting length, attendees, concurrent meetings) belong to the merchant's own plan. The provider's message is shown in `last_error`.
