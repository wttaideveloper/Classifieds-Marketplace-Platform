# Notification service

One service that sends notifications and nudges for every module of the platform. It has its own database and its own container, and
knows nothing about the other modules: they send it **events**, it decides who gets what, on which channel, and when.

Status: first version. Tested with its own test suite on SQLite. Not yet run against PostgreSQL, a real Firebase project or a real SMTP server.

## Run it

```bash
cd notification-service
pip install -r requirements.txt
cp .env.example .env        # set PRODUCER_KEYS and ADMIN_KEY at least
uvicorn app.main:app --port 8080
```

Docker: `docker build -t notification-service . && docker run --env-file .env -p 8080:8080 notification-service`.
Interactive docs: `http://localhost:8080/docs`. Health: `GET /health`.

With no Firebase or SMTP settings it only **logs** what it would send, so a developer machine never sends a real message.
Tables are created on start-up for a quick start. For production use Alembic migrations (not included yet) on a PostgreSQL `DATABASE_URL`.

## The idea

1. An admin registers **event types** once: `payment.succeeded` has a title and body with `{placeholders}`, a category, channels and a priority.
2. A module sends `POST /v1/events` with the event name, who should get it and the values.
3. For each person the service stores a **notification** (their inbox), then queues a **delivery** for each other channel they allow (push, email).
4. A worker sends the deliveries, retrying failures with a growing wait (30s, 60s, 120s ...), and gives up after `MAX_ATTEMPTS`.
5. **Nudges** are rules on events: "when `order.created` happens and `payment.succeeded` does not follow within 60 minutes, send `order.payment_reminder`".

Authentication: header `X-API-Key`. Each calling service has its own key in `PRODUCER_KEYS` (`name:key,...`), so one can be revoked alone.
`ADMIN_KEY` unlocks `/v1/admin/*`. The service trusts the caller about *who the user is*: the apps talk to their own backend, which talks to this
service with its key. It has no end-user login.

## API (all under `/v1`)

| | |
|---|---|
| `POST /events` | send an event to up to 1000 people. 202. Same `idempotency_key` for the same person = sent once |
| `GET /users/{id}/notifications` | the inbox: `unread_only`, `category`, `tenant_id`, `page`, `page_size`; returns `unread_count` |
| `GET /users/{id}/notifications/unread-count` | the badge number |
| `POST /users/{id}/notifications/read` | `{"ids": [...]}` or `{"all": true}` |
| `POST` / `DELETE /users/{id}/devices` | register / remove a push token |
| `GET` / `PUT /users/{id}/preferences` | per category and channel (push, email) on or off; the inbox is always kept |
| `GET /admin/event-types`, `PUT /admin/event-types/{event}` | wording, category, channels, priority |
| `GET` / `POST /admin/nudge-rules`, `PATCH /admin/nudge-rules/{id}/active` | nudges |
| `GET /admin/deliveries?status=` | what was sent, what is waiting, what gave up |

Send:

```json
POST /v1/events
{ "event": "payment.succeeded", "recipients": [ { "user_id": "u-123", "email": "priya@example.com" } ], "tenant_id": "t-1",
  "idempotency_key": "payment-97eb:succeeded", "data": { "amount": "1180.00", "currency": "INR", "payment_id": "97eb" } }
```
Answer: `{"results": [ {"user_id": "u-123", "status": "created", "notification_id": "…", "channels": ["in_app","push","email"]} ]}`.
`status` is `duplicate` when the key was seen before. An event with no registered type is 422, unless the call carries its own `title`.

Rules worth knowing: a `critical` event ignores preferences (security alerts); `email` is only used when the call sends the person's `email`;
a person with no registered device gets a `skipped` push; a token that Firebase says is dead is removed.

## Using it from another module

Copy `client/notification_client.py` (needs only `httpx`):

```python
notify = NotificationClient("https://notifications.internal", api_key="payments-key")
notify.send("payment.succeeded", [user_id], data={"amount": "1180.00", "currency": "INR"}, idempotency_key=f"payment-{payment_id}:succeeded")
```
A failure is logged and swallowed, so a notification never breaks the action that caused it.

## What is not here yet

- **Redis / queue.** The queue is the database (the `deliveries` table) and the worker is a thread inside the API process. It is simple and
  survives restarts. For high volume, run the worker as its own process or move the queue to Redis. Two API containers running workers at once could
  send the same delivery twice: use one worker until delivery claiming (`SELECT … FOR UPDATE SKIP LOCKED` on PostgreSQL) is added.
- **Realtime (WebSocket) push to open apps.** Apps poll the inbox, or the owning backend relays.
- **SMS and WhatsApp.** The channel list is built to take them; no provider is written.
- **Quiet hours, digests, per-tenant branding, localisation of templates.**
- **Alembic migrations, metrics, rate limits.**
- **Moving this platform's existing notifications over.** Nothing in the main backend calls this service yet. See `docs/design.md`.
