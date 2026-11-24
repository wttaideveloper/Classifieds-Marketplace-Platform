# Moving from the monolith to v2 — data migration and rollback

Nothing in this document has been run against a real database. Every step that writes is explicit and
needs a person to run it; the tools default to dry runs.

## 0. Before you start

* Back up the monolith database (`pg_dump -Fc`). The monolith's schema is **never changed** by v2.
* Create a **read-only** PostgreSQL user on the monolith database for the copy step.
* Fill `microservices-v2/.env` from `.env.example` (same secrets the monolith uses, plus `INTERNAL_API_KEY`).

## 1. Create the 11 new databases and their tables

```
cd microservices-v2
docker compose up -d db redis                        # creates identity_db ... moderation_db (docker/init-databases.sql)
docker compose --profile init run --rm schema-init   # create_schema for every service; skips a database that already has tables
```

Outside compose, per service: `DATABASE_URL=<its new db> python -m <svc>_service.create_schema`.
Each service database also gets `outbox_messages` / `processed_messages` (D3) and, where it holds replicas,
the replica tables (enterprises, locations, products, services, events, trainings, ...).

## 2. Copy data (per service, during a write freeze)

```
# dry run: counts what would be copied, writes nothing
DATABASE_URL=<new db> python -m <svc>_service.copy_data --source-url <monolith db, read-only user>
# real copy
DATABASE_URL=<new db> python -m <svc>_service.copy_data --source-url <...> --execute
```

* Copies only the tables the service owns (`model_registry.OWNED_TABLES`), verifies row counts, never
  writes to the source.
* Order: catalog first (it publishes replicas), then events and learning (they publish replicas to
  communication), then the rest.
* Replicas are filled by the owners: after the copies, start the services; subscribers pull a full
  snapshot at start-up and every `REPLICA_SYNC_SECONDS`.
* Stock reservations (D1) start empty; open checkouts at freeze time are simply not reserved.

## 3. Switch traffic

Start every service and the gateway, check `GET /_gateway/ready`, then point the load balancer at the
gateway instead of the monolith. Paths, methods and responses are the same (see `ENDPOINT_INVENTORY.md`).
Socket.IO runs on communication (one worker) and is relayed through the gateway.

## 4. Rollback

* **Before** any write reached v2: point the load balancer back to the monolith. Nothing else to do.
* **After** v2 took writes: those writes exist only in the 11 service databases. Stop the gateway, freeze
  writes, and copy the changed rows back per table (each service owns disjoint tables, so the copy is a
  per-table upsert into the monolith's identical schema; v2 adds only `outbox_messages`,
  `processed_messages` and `stock_reservations`, which are not copied back). Then switch the load balancer
  back. No reverse-copy tool is provided yet — **plan a short write window or a rehearsal before going
  live**.
* The monolith code and configuration are unchanged in this branch, so redeploying it is the same as today.

## What to watch after the switch

* `outbox_messages` rows with `status = 'dead'` (commerce, events): a confirmation/restock that failed
  `OUTBOX_MAX_ATTEMPTS` times; replay by setting them back to pending.
* `stock_reservations` older than 15 minutes that were never confirmed are released automatically.
* Logs carry `X-Request-ID` across the gateway and every internal call.
