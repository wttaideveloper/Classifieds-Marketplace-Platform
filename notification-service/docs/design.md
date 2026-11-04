# Notification and nudge service: design

For the team and the client. Plain language first, details after.

## 1. Why a separate service

Today notifications are written inside the marketplace backend. About fifteen modules each have their own notification code (chat, payments,
training, events, blogs, reviews ...) and 18 call sites use one shared helper. Delivery (Firebase push, email, the in-app feed) is part of the same
application and the same database. That means:

- a new product (a second app, a clinic system) has to copy or import the marketplace to notify anyone;
- reminders ("you have not paid", "your training deadline is near") are scheduled jobs scattered across modules;
- a push or email problem can slow or break the business action that triggered it;
- nobody can see, in one place, what was sent to whom and why.

The new service owns all of that behind one small API, and anything that can make an HTTPS call can use it.

## 2. What it does

| | |
|---|---|
| **Receives events** | "payment.succeeded for user X with these values" from any module, with its own API key |
| **Builds the message** | from a template registered once (title and body with `{placeholders}`, category, channels, priority) |
| **Keeps an inbox** | one notification per person, readable and markable as read |
| **Delivers** | in-app always; push and email through a queue, retried, respecting each person's preferences |
| **Nudges** | rules that fire a reminder when an event is not followed by another within a time |
| **Reports** | every delivery with its status, attempts and last error |

Not in the service: knowing who users are, deciding *whether* a module should notify, and the apps' screens.

## 3. Architecture

```
 modules / apps                         notification service                      outside world
 ----------------                       --------------------------------          --------------
 payments  --\                          API  POST /v1/events                      Firebase (push)
 training  ---+-- HTTPS + API key -->        |                                    SMTP (email)
 chat      --/                              v
 any new one                          notifications (inbox)  +  deliveries (queue) ---> worker --> providers
                                            ^                       ^
 app backend --- GET inbox, mark read ------+          nudge rules / jobs --- fire when due
```

- **API** (FastAPI). Stateless, so several copies can run.
- **Database** of its own (PostgreSQL in production). Tables: event types, notifications, deliveries, devices, preferences, nudge rules, nudge jobs.
- **Worker.** Each round it fires due nudges, then sends due deliveries. A failed delivery waits 30s, 60s, 120s ... and fails for good after 5 tries.
- **Providers.** Push (Firebase Cloud Messaging) and email (SMTP) behind a small interface, so another provider is a new class.
- **Independence.** Callers use only HTTP and an API key. The service has no import of, or database link to, any module.

## 4. How a module uses it

1. Once, an admin registers the event type: `PUT /v1/admin/event-types/payment.succeeded` with the wording and channels.
2. The module calls `POST /v1/events` after its own change is committed. Give a stable `idempotency_key`: a retry then never sends twice.
3. The module's own backend shows the inbox for its app through `GET /v1/users/{id}/notifications` and `POST .../read`.
4. When someone signs in on a phone, the backend registers the push token; on sign-out it removes it.

The call should not be allowed to break the business action: the supplied client swallows and logs failures, and for important events the
module can keep its own outbox and retry.

## 5. Nudges

A rule has: a trigger event, an optional cancel event, the event to send, a delay, and how many times to repeat.

Example: `order.created` triggers `order.payment_reminder` after 60 minutes, cancelled by `payment.succeeded`, up to 2 reminders a day apart.
It works per person and per subject (here the order id), so one person paying order A does not stop the reminder for order B, and nobody else's
payment cancels it. A switched-off rule starts nothing. A reminder goes through the same pipeline as any event, with the same preferences,
inbox and delivery.

Nudges the platform needs today (training deadlines, payment retry reminders, unpaid orders, event reminders, expiring offers) can each
become a rule instead of a scheduled job in its module.

## 6. Preferences, safety and privacy

- A person can switch push or email off per category. The inbox is always kept. A `critical` event (a security alert) ignores preferences.
- One API key per calling service; the admin key is separate. Keys can be changed without code.
- The service stores titles, bodies and the `data` it was sent. **Callers must not put secrets or full personal records in `data`.**
  Nothing sensitive (for example a review reason) should be in a push; keep that in `data` for the inbox only, or leave it out.
- Email addresses are sent by the caller with each call and used for that delivery. The service keeps them only on the delivery row.
- Push tokens are stored per person and removed when Firebase says they are dead or the person signs out.

## 7. Moving the marketplace over

Nothing in the marketplace calls this service yet. Suggested order:

1. Run the service next to the marketplace. Register the event types of one module (payments).
2. In the marketplace, replace the body of the shared helper `create_automatic_notification` with a call to the service (or a switch that
   does both for a while), so the 18 callers move without edits.
3. Move the inbox endpoints of the apps to read from the service; keep the old tables read-only until you are sure.
4. Move the scheduled reminders (training deadlines, payment retries) into nudge rules, one at a time.
5. Retire the old notification tables and the Firebase code from the marketplace.

What can go wrong: duplicate notifications while both systems send (use the switch, one module at a time); user ids that differ between systems
(the service treats them as plain strings, so use the marketplace's user id everywhere); lost history (migrate or keep the old table read-only).

## 8. What is built, what is not

Built and tested (12 tests, SQLite): events, templates, idempotency, inbox, read state, devices, preferences, retry and give-up, dead-token
cleanup, nudges (fire, cancel, repeat, no double start), admin API, API keys.

Not built or not verified:
- Real Firebase and SMTP delivery were never exercised.
- PostgreSQL was never used; the code is plain SQLAlchemy and should work, but must be tried.
- No Redis queue. The database is the queue. One worker only, until delivery claiming for several workers is added.
- No Alembic migrations, metrics, rate limiting, SMS, WhatsApp, realtime push to open apps, quiet hours, digests or template translations.
- The marketplace does not use it yet.

## 9. Questions for the client

1. Which channels are needed on day one: push, email, SMS, WhatsApp?
2. Which products will use it besides the marketplace?
3. Where will it be hosted, and who runs the database?
4. Which reminders matter most (payment, training deadline, events, unpaid orders)?
5. Are there quiet hours or limits on how many nudges a person may get a day?
6. Which languages must the messages support?
