# Third-party dependencies: Virtual Meeting Providers and Booking & Scheduling

This lists everything outside our own code that has to be in place before the virtual meeting integrations (Zoom, Google Meet, Microsoft Teams) and the booking system with calendar sync (Google Calendar, Outlook) can work. For each one it says what we use it for, what someone has to set up, and who has to do it.

Nothing here has been built against the live providers yet. Names of consoles, scopes and limits are what each provider documents today and should be re-checked when setup starts, because providers change them.

## 1. What we already have

| Item | Where | Useful for |
|---|---|---|
| `requests` HTTP client | requirements.txt | Calling provider APIs |
| `redis` | requirements.txt | Locks, short-lived state, rate-limit counters |
| `celery` | requirements.txt | Background sync and retries (needs a worker and broker running) |
| `tzdata` | requirements.txt | Time zone database, needed for correct local times |
| SMTP settings | `SMTP_HOST`, `SMTP_PORT` | Booking confirmation and reminder emails |
| Firebase credentials | `FIREBASE_CREDENTIALS_*` | Push notifications for booking changes |
| Google OAuth client settings | `GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI` | Existing sign-in. Do not reuse it for calendar access (see 3.1) |
| ICS file generation | `app/services/calendar_service.py` | "Add to calendar" files. It does not sync anything |
| Meeting link field on events | `docs/event-meeting-links.md` | Today a link is typed in by hand. Nothing creates a meeting |

## 2. Accounts and registrations (one-time, done by us)

These are the external dependencies that block everything else. Each takes calendar time because of review or verification, so start them first.

### 2.1 Zoom
- A Zoom Marketplace developer account.
- A Marketplace app of the OAuth type that lets other Zoom accounts install it (a user-managed app), because each provider connects their own Zoom account.
- Scopes needed: create, read, update and delete meetings for the connected user, and read their profile. Add recording scopes only if recordings are in scope.
- Redirect URL registered for our API, one per environment.
- A webhook endpoint and secret token if we want to hear about meetings that are changed or deleted inside Zoom.
- Review by Zoom before the app can be installed by accounts outside ours. Allow several weeks. Before approval it only works for our own account.
- Needs: Client ID, Client Secret, webhook secret token.

### 2.2 Google (Meet and Calendar)
- A Google Cloud project, with the Google Calendar API enabled. Meet links are created as part of a Calendar event, so no separate Meet API is needed for links.
- An OAuth consent screen and a web OAuth client, with a redirect URL per environment.
- Scopes: calendar events access (read and write) and, for free/busy checks, calendar read. Keep scopes as narrow as possible.
- These calendar scopes are classed as sensitive. Google requires app verification before more than a small number of outside users can connect, and may ask for a privacy policy page, a homepage on a verified domain, and a demo video. Allow several weeks.
- Until verified, the app is in "testing" mode: limited to listed test users, and their access expires after a short period.
- Needs: a new Client ID and Secret just for calendar (separate from the sign-in client), a stable offline refresh token per connected user.
- Push notifications from Google Calendar (so changes made in Google reach us) need a public HTTPS endpoint on a verified domain, and the watch has to be renewed before it expires.

### 2.3 Microsoft (Teams and Outlook calendar)
- A Microsoft Entra ID (Azure AD) app registration, multi-tenant, so providers from any organisation or personal Microsoft account can connect.
- Microsoft Graph delegated permissions: calendar read/write, online meetings create (for Teams links), user profile read, and offline access so we get a refresh token.
- Some of these permissions need an admin of the provider's organisation to approve them. Many company tenants block user consent, so some providers will not be able to connect without their IT admin. Plan for that message in the UI.
- A client secret or certificate, which expires. Someone must own the renewal date (secrets last at most a couple of years and a calendar reminder is required).
- Publisher verification is recommended so the consent screen does not show "unverified".
- Change notifications from Graph need a public HTTPS endpoint and a subscription that has to be renewed before it lapses.
- Needs: Application (client) ID, client secret, redirect URLs.
- Teams meetings through Graph require the provider to have a Teams licence. Personal Microsoft accounts have limited or no Teams meeting creation.

## 3. Libraries to add

All optional in the sense that the providers' plain REST APIs can be called with `requests`. These save work, especially around token refresh and time zones.

| Library | Use | Note |
|---|---|---|
| `google-api-python-client`, `google-auth`, `google-auth-oauthlib` | Google Calendar and Meet | Official Google clients |
| `msal` | Microsoft sign-in and token refresh | Official Microsoft library. Calls to Graph itself can use `requests` |
| `cryptography` | Encrypting stored refresh tokens | Required, see section 5 |
| `python-dateutil` | Recurrence rules and date maths | For repeating availability and bookings |
| `icalendar` or `ics` | Reading and writing ICS | Only if invites by email or ICS import are in scope. Our existing ICS code is hand-written |
| `tenacity` or similar | Retry with backoff | Provider calls fail and rate-limit |

Zoom has no official Python library worth depending on. Use `requests`.

Pin exact versions, and have someone check each library's licence and security history before adding it.

## 4. Infrastructure

- **Public HTTPS URL for the API** in every environment, on a stable domain. Needed for OAuth redirects and provider webhooks. Localhost works for OAuth redirects only, not webhooks.
- **A worker process and broker** (Celery with Redis, both already listed) running in production. Calendar sync, webhook processing, token refresh and retry should not happen inside web requests. Today the repo uses in-process threads for some jobs; a sync that must not be lost needs a durable queue.
- **Scheduled jobs** for: renewing Google/Microsoft webhook subscriptions, refreshing tokens before expiry, catching up on missed webhooks, and sending reminders.
- **Secret storage.** Client secrets and the key that encrypts refresh tokens must live in the environment or a secrets manager, never in the repository.
- **Outbound email** (SMTP already configured) with a sender domain that has SPF and DKIM set up, so confirmations are not marked as spam.
- **Time zone data.** Keep `tzdata` current. Store times in UTC with the person's time zone name, never just an offset.
- **Monitoring and alerts** for failed syncs, expired connections and webhook errors, since a silent failure means a double booking.

## 5. Data and security obligations that come with these dependencies

- Refresh tokens give long-lived access to a provider's calendar. Store them encrypted, one set per user, and never return them in any API response or log.
- Store the minimum calendar data. For availability checks, free/busy is enough; avoid copying event titles and attendees of the user's private appointments.
- A user must be able to disconnect. Disconnecting should revoke the token at the provider where possible and delete ours.
- Privacy policy and terms must describe the calendar and meeting access. Google and Microsoft check this during verification.
- If clinical or patient information is involved, do not put patient names or reasons for the visit in the title or description of events or meetings created in the provider's account. Provider accounts are outside our control.
- Zoom, Google and Microsoft each have their own developer terms, including limits on how data from their APIs can be stored or used. Someone should read them before launch.
- Webhook calls from providers must be verified (Zoom signature and token, Google channel token, Microsoft client state) and processed once only.

## 6. Limits to plan for

- Each provider rate-limits API calls per user and per app. A bulk sync for many users has to be spread out and retried with backoff.
- Meeting creation can fail for reasons we cannot fix: licence missing, token revoked, free-plan time limits on Zoom meetings, admin policy blocking the app. The booking must still succeed or fail clearly, and the user must be told to reconnect.
- Provider webhooks can arrive late, twice, or not at all. Keep a periodic reconciliation job.
- The connected account belongs to the provider (the clinician), not to us. If their licence or account ends, their future meetings stop working.

## 7. Cost and licences

- Zoom, Google Calendar API and Microsoft Graph calendar calls are free to call within quota at the time of writing, but the connected user needs the right account: a Zoom licence for meeting features beyond the free limits, Google Meet via their Google account, and a Microsoft 365 licence with Teams for Teams meetings.
- Hosting for the worker, broker, and any extra storage.
- Possible paid verification steps. For example, some Google sensitive scopes are free to verify, but restricted ones can require a paid security assessment. Confirm which scope category our final scope list falls into before committing to dates.

## 8. Setup checklist and owners

| Step | Owner | Lead time |
|---|---|---|
| Decide which providers ship first | Product | now |
| Public domain, HTTPS, privacy policy and terms pages | Business / Legal | days |
| Zoom Marketplace app created and submitted for review | Backend / Admin | several weeks |
| Google Cloud project, consent screen, verification | Backend / Admin | several weeks |
| Entra app registration, admin-consent wording, publisher verification | Backend / Admin | days to weeks |
| Redirect URLs and webhook URLs per environment | Backend / DevOps | days |
| Secrets in the secrets manager, encryption key created | DevOps | days |
| Worker, broker and scheduler running in production | DevOps | days |
| Email domain SPF and DKIM | DevOps | days |
| Test accounts for Zoom, Google Workspace and Microsoft 365 | QA | days |
| Secret and certificate expiry reminders set | DevOps | same day |

## 9. Things only the product owner can decide

- Which providers launch first. Google Calendar alone is the shortest path; Zoom and Microsoft add review and admin-consent steps.
- Whether sync is one-way (our bookings go into their calendar) or two-way (their busy times block our availability, and edits flow back). Two-way needs webhooks and reconciliation and is much more work.
- Whether the platform owns one shared Zoom account for all meetings, or each provider connects their own. A shared account is simpler to launch but has concurrent meeting limits and puts all meetings under one licence.
- Whether recordings are in scope. They add scopes, storage and consent requirements.
