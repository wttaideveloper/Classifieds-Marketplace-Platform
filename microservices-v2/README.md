# Marketplace microservices v2 — own code, own database per service

The monolith (`../app`) is split into **11 domain services**. Each contains only its own code and owns its
own database; anything it needs from another domain goes through that domain's internal HTTP API
(`/internal/v1`, shared `INTERNAL_API_KEY`), a read-only replica pushed by the owner, or a durable outbox.
A gateway keeps the monolith's paths, so clients see one base URL and the same API. The monolith code and
configuration are unchanged and remain the rollback path.

## Run it

```
cd microservices-v2
cp .env.example .env                                  # placeholders; fill in locally
docker compose up -d db redis
docker compose --profile init run --rm schema-init    # first run: tables in the 11 NEW databases
docker compose up -d --build
curl http://localhost:8000/_gateway/ready
```

| Component | Port (host) | Database |
|---|---|---|
| gateway | 8000 | – |
| identity / catalog / booking / events / learning / commerce | 8201–8206 | `<name>_db` |
| communication (Socket.IO, 1 worker) / notifications / content / meetings / moderation | 8207–8211 | `<name>_db` |
| PostgreSQL / Redis | 55433 / 56380 (localhost only) | – |

Example requests through the gateway (same as the monolith):

```
curl http://localhost:8000/api/v1/products/
curl -H "Authorization: Bearer <token>" http://localhost:8000/api/v1/events/
curl -H "Authorization: Bearer <token>" -X POST http://localhost:8000/api/v1/cart/checkout -H "Content-Type: application/json" -d '{...}'
```

Environment variables: `.env.example` (the monolith's settings + the v2 section). Data copy and rollback:
`docs/DATA_MIGRATION.md`. Every route and its owner: `docs/ENDPOINT_INVENTORY.md`. Cross-database designs
D1–D8 (approved and built): `docs/CROSS_DB_DESIGNS.md`.

## Status (2026-11-22)

All 11 services are extracted; none imports the monolith (`tools/extract.py` reports 0 remaining imports).

| Service | Owns | Cross-service mechanisms | Comparison with the monolith (isolated SQLite DBs, stubs for other services) |
|---|---|---|---|
| identity | users/auth | enterprise↔tenant via catalog | 17 flows + generated sweep: 0 differences |
| notifications | notifications, dedupe, records | delivery profile via communication; Redis real-time | 19 ops: 0 differences |
| content | blogs, uploads, CMS | enterprises via catalog; media rules via learning | 85 ops: 0 differences |
| catalog | enterprises, locations, products, services, reviews | publishes replicas; stock reservations (D1) | 51 ops + 31-step flow: 0 differences |
| events | events, registrations, orders | meetings (D7); event-order API for payments (D2); publishes replicas to communication | 155 ops: 0 differences |
| learning | trainings, courses, programs, enrolments | meetings (D7); publishes replicas to communication | 414 ops: 0 differences |
| commerce | cart, orders, payments | reservations (D1); event-order confirm via outbox (D2, D3) | 95 ops: 0 differences |
| booking | service bookings | catalog replicas + reads (D4) | 14 ops: 0 differences |
| meetings | meeting records, providers | hooks from events/learning (D7) | 24 ops: 0 differences |
| moderation | review audit log, admin diagnostics | review queue merged from owners (D6) | 5 ops: 0 differences |
| communication | chat, groups, devices, Socket.IO | events/learning replicas (D5), names (D8) | 132 ops: 0 differences |

**Integration** (`tests/integration`): all 11 services behind the real v2 gateway, each on its own database,
internal calls and replica pushes for real, compared step by step with the monolith: event flow, training
flow, checkout with stock reservation (D1), paid event orders (D2), bookings on catalog (D4), meetings with
owners (D7), group chats / review moderation / approvals (D5, D6) — **14 tests, 0 differences**.
Latest full run: see "Test results" below.

## Layout

```
kit/mskit/                shared plumbing (no domain logic): settings (the monolith's settings class),
                          auth (token validation, roles, catalog access policy, tenant lookup, IdP recipients),
                          errors, HTTPS links, correlation ids, logging, internal API client + typed clients,
                          Redis real-time emitter, pure image inspection, data copy
gateway/                  routes by the monolith's route order (routes.json); serves the API docs from a monolith snapshot; hides /internal
services/<name>/
  <name>_service/         the service's own copy of its monolith modules (same layout, imports rewritten)
    db/database.py        its own engine + Base (DATABASE_URL = its own database)
    main.py               entry point (routers in monolith order)
    internal_api.py       /internal/v1 endpoints other services call (shared-key protected)
    copy_data.py          copy its tables out of the monolith database (dry run by default)
    create_schema.py      create its tables in a new, empty database
  extract.json            which monolith modules/tables it owns
  tests/                  contract tests + generated comparison with the monolith
tools/
  plan_extract.py         import-graph plan: what a service must own, every edge into other domains
  extract.py              copy + rewrite imports; lists remaining monolith imports
  scaffold.py             generates db/main/copy/schema/Dockerfile/tests for a service
  domains.py              which service owns each monolith module and table (134 tables, each once)
tests/support/            in-process comparison library + transport-level stubs of other services
```

## How a service is extracted (repeatable)

1. `python tools/plan_extract.py <svc> --write` — owned modules + every cross-domain edge.
2. `python tools/extract.py <svc>` — copies them; prints remaining monolith imports.
3. For each remaining import: add an internal endpoint on the owning service + a typed client in
   `kit/mskit/clients.py`, and replace the call (comment says what it replaced).
4. `python tools/scaffold.py <svc>`, then `cd services/<svc> && python -m pytest`.

## Internal APIs (main ones)

| Owner | Endpoint (`/internal/v1`) | Replaces | Implemented |
|---|---|---|---|
| notifications | `POST /notifications/automatic` | `notification_service.create_automatic_notification` | yes |
| notifications | `POST /notification-dedupe/claim`, `/release` | `notification_idempotency.claim/release` | yes |
| communication | `GET /users/{id}/delivery-profile` | `chat_repo.get_notification_preferences` + `get_active_device_tokens` | yes |
| learning | `POST /training-media/upload-field`, `/training-media/validate` | `training_form_media` in generic uploads | yes |
| catalog | `GET /enterprises/{id}/tenant`, `GET /tenants/{id}/enterprise`, `POST /enterprises/business-names` | Enterprise queries in auth and blogs | yes |
| catalog | `GET /replica/{topic}` (snapshot) → subscribers `POST /replica/{topic}` (push) | FK/relationship reads of enterprises + locations | yes (events, learning, commerce, booking, communication) |
| notifications | `POST /notifications/records`, `POST /notifications/mark-read-by-reference` | audit rows of notification_triggers; discussion read-tracking | yes |
| meetings | `/hooks/{events,trainings}/*`, `/providers/placeholders-allowed`, `/records/is-generated-link` | meeting_service hooks called with ORM objects | yes (D7) |
| commerce | `GET /users/{id}/confirmed-purchases/{product_id}` | verified-purchase query on orders | yes |
| moderation | `POST /review-log` | review_audit.record | yes |
| catalog | `POST /stock/reservations`, `/stock/reservations/{id}/confirm`, `POST /stock/releases` | stock decrement inside checkout | yes (D1) |
| events | `GET /event-orders/{id}`, `POST .../payment-confirmed`, `.../refunded` (idempotent) | payment_payables writing event_orders | yes (D2, via outbox) |
| catalog, learning, events | `POST /reviews/moderation-slice`, `/reviews/{module}/{id}/moderate`, `/delete` | review_admin_service over 4 tables | yes (D6) |
| owners of names | `POST /names` | cross-domain display-name lookups | yes (D8) |
| events, learning | `GET /replica/{topic}` → communication | group chat reads of registrations/enrolments | yes (D5) |

All calls: shared `INTERNAL_API_KEY`, `X-Request-ID` propagated, connect 2 s / read 10 s, refused
connections retried (nothing was received), POST/PATCH never retried after sending, owner errors
re-raised as the same `HTTPException`.

## Reference data replication (kit/mskit/replication.py)

Tables other domains have foreign keys to (enterprises, enterprise_locations) are replicated **read-only**
into the services that reference them, so their ORM relationships and responses stay unchanged. Catalog
pushes changed rows to every subscriber right after its commit (inside the request), and subscribers pull a
full snapshot every `REPLICA_SYNC_SECONDS` (default 300) when their hash differs. Writing a replica outside
catalog raises `ReplicaWriteError`. Configuration: `REPLICA_SUBSCRIBERS=events,learning,...` on catalog.


## Behaviour differences (deliberate, documented)

1. **Notification de-duplication rollback** — the monolith's `claim` rolled back the caller's whole session on
   a duplicate key, discarding the caller's uncommitted changes. v2 commits the caller's changes first, so
   they are kept on the duplicate path too.
2. **Transactions across services** — writes that shared one transaction are now commits in two databases.
   Side effects that must happen (event-order confirmation after payment, restock after cancellation) go
   through the **outbox** (D3) and are retried until delivered; receivers are idempotent
   (`processed_messages`).
3. **Stock (D1, approved)** — checkout reserves stock in catalog, creates the order in commerce, then
   confirms the reservation. If commerce stops mid-checkout the stock is held for up to 15 minutes, then
   released automatically.
4. **Paid event orders (D2, approved)** — after a successful payment the event order can show "pending" for
   a short time until the outbox delivers the confirmation to events.
5. **Group chats (D5, approved) and other replicas are eventually consistent** — updated right after the
   owner's commit (push); if a subscriber was down it catches up at the next snapshot sync (default 5 min).
6. **Meetings unavailable** — meeting hooks never fail an event/training request (as before); while meetings
   is down, `placeholders_allowed` and `is_generated_link` answer "no".
7. **Review history rows and discussion read-marks** are sent right after the commit, not via the outbox: a
   crash between the commit and the call loses that history row / read-mark.
8. **Admin diagnostics** — `realtime_loop_attached` now reports whether Redis is configured (the Socket.IO
   loop lives in communication, not in the process answering the request).

## Test results (2026-11-22, Windows, Python 3.13, SQLite per service)

| Suite | Result |
|---|---|
| identity | 26 passed |
| notifications, content, catalog, events, learning, commerce, booking, meetings, moderation, communication | 7 passed each (contract tests + one comparison test that sweeps every operation of the service) |
| gateway | 31 passed (2 expectations updated: admin event routes are owned by events now) |
| kit (outbox) | 5 passed |
| integration (all 11 services + gateway vs monolith) | 14 passed |

Per-service comparison sweeps: 0 differences in every service (operation counts in the status table).

How the comparison works: the monolith and the v2 service run in the same test process, each on its own
fresh SQLite database; the same requests (hand-written flows + requests generated from the OpenAPI schema
for every operation) go to both; responses are compared after normalising ids, timestamps and tokens.
Other services are replaced by transport-level stubs in per-service tests, and are real in the integration
tests. External systems (SMTP, payment gateways, Firebase, Zoom/Google, Keycloak) are local stubs; no real
e-mail, payment or push notification is sent.

## Not verified / known gaps

* **PostgreSQL and Docker were not available** on this machine: `docker-compose.yml`, the Dockerfiles and
  `schema-init` were written but never built or started; all tests ran on SQLite (with a JSONB→JSON shim in
  tests only). Run the compose stack and the integration tests against PostgreSQL before going live.
* Generated requests mostly hit validation / auth / not-found paths; happy paths are covered by the
  hand-written flows and integration tests, not exhaustively for all 1,042 routes.
* Socket.IO through the gateway was tested at the relay level (gateway tests) and for the server-side
  emitters, not with a real browser client over the full stack.
* Background jobs (reminders, outbox relay, replica sync, reservation expiry) were tested by calling them
  once, not on their real schedules.
* No reverse-copy tool for rollback after v2 has taken writes (see `docs/DATA_MIGRATION.md`).
* Load, concurrency and failure-injection (service killed mid-request) were not tested beyond unit tests of
  the outbox and retry policy.
* The stage-1 folder `microservices/` is no longer in the working tree (it was untracked and was removed
  outside this work); v2 does not depend on it.
