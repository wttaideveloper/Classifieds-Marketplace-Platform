# Event type and modules: backend contract (Phase 2.2)

An Event is one configurable system. `event_type` says what kind of event it is; `modules` says which
capabilities it actually has. **Phase 2.2 stores and returns this configuration only — no endpoint
enforces it yet** (registration, check-in, sessions, checkout etc. behave exactly as before).

## Fields

| Field | Create / Update request | Response |
|---|---|---|
| `event_type` | optional; one of `conference`, `workshop`, `marathon`, `camp`, `private_function`, `webinar`, `other` (exact, lower-case) | always present; legacy events resolve to `"other"` |
| `modules` | optional object of booleans (partial allowed) | always present; always all eight keys |

Modules: `registration`, `tickets`, `sessions`, `check_in`, `online_meeting`, `custom_questions`, `meals`,
`accommodation`. Unknown names and non-boolean values (including `null`) are rejected with 422.
`event_type`/`modules` appear on every event response (list, detail, create, update, duplicate, status
change, search). They are independent of `status` (workflow) and `lifecycle_state` (time-based).

## Defaults by type (applied once, when the event is created)

| Type | registration | tickets | sessions | check_in | online_meeting | custom_questions | meals | accommodation |
|---|---|---|---|---|---|---|---|---|
| conference | ✔ | ✔ | ✔ | ✔ | | | ✔ | ✔ |
| workshop | ✔ | ✔ | ✔ | ✔ | | | | |
| marathon | ✔ | ✔ | | ✔ | | | | |
| camp | ✔ | | | ✔ | | | ✔ | ✔ |
| private_function | ✔ | | | ✔ | | ✔ | ✔ | |
| webinar | ✔ | | ✔ | | ✔ | | | |
| other | ✔ | | | | | | | |

The defaults are copied into the event. They are never recalculated on read, and changing the table later
does not change existing events. A paid event (paid pricing, a price, or priced ticket types) always keeps
`tickets: true` when defaults are applied.

## Create

- `event_type` + no `modules` → that type's defaults.
- `event_type` + `modules` → the defaults with the given modules applied on top (`{"meals": false}` turns meals off).
- `modules` only → the event's behaviour-based modules (see Legacy) with the given modules on top; `event_type` stays unset.
- neither → the event stays "legacy" (nothing stored). Clients that do not know about this feature are unaffected.

## Update (PUT; partial semantics)

`null` or omitted means **no change**, so an update that does not mention configuration never resets it, and
a client that serializes absent values as `null` cannot wipe it. There is no "clear" operation.

- `modules` given → **merged** over the current configuration; modules you do not send keep their value.
- `event_type` given → stored. It **never rewrites an already-configured `modules`**.
  Only an event that was never configured gets the new type's defaults, and only when the type really
  changes. Sending back the `"other"` read from a legacy event changes nothing.

## Validation

Only obvious contradictions in an *explicit* request are rejected (422 with a message). Values that come
from a type default are never rejected.

- `online_meeting: true` needs `delivery_mode` `online` or `hybrid` (on update, the resulting delivery mode).
- `tickets: false` is refused for a paid event.
- Disabling `sessions` never deletes session data.

## Legacy events (created before Phase 2.2)

Both columns are NULL and are **not backfilled**. On read they are resolved from what the event does today:
`registration` and `check_in` are on; `tickets` if the event is paid or has ticket types; `sessions` if it has
sessions; `online_meeting` if `delivery_mode` is online/hybrid or a meeting link/provider is set;
`custom_questions` if it has registration questions, custom values, or is pinned to an Event Form version;
`meals` and `accommodation` are off. Nothing an existing event already uses is disabled, and nothing is
written by a read. Persisted values always win over this derivation.

## Database

`events.event_type` `VARCHAR(30)` NULL and `events.modules` `JSONB` NULL (Alembic `adae1a2909cc`, chained on
`e2b1c2d3e4f7`). Additive and nullable, with no default and no backfill.
