# Event type and modules: backend contract (Phase 2.2, evolved to dynamic configuration)

An Event is one configurable system. `event_type` says what kind of event it is; `modules` says which
capabilities it actually has. Event Types are no longer a hardcoded list: they are backend-authoritative
configuration rows (`/api/v1/event-types`), so a Super Admin can add, rename, retire, or change the module
rules of an Event Type without a code change. **Phase 2.2 stores and returns `modules` only — no endpoint
enforces it against event behaviour** (registration, check-in, sessions, checkout etc. behave exactly as
before); Event Types themselves ARE enforced (existence, active status, allowed/required modules) at
event create/update time.

## Fields

| Field | Create / Update request | Response |
|---|---|---|
| `event_type` | optional; a key naming an existing, **active** Event Type (see below) | always present; legacy events resolve to `"other"` |
| `modules` | optional object of booleans (partial allowed) | always present; always all eight keys |

Modules: `registration`, `tickets`, `sessions`, `check_in`, `online_meeting`, `custom_questions`, `meals`,
`accommodation` — unchanged since Phase 2.2, and not renamed or extended by this evolution. Unknown names
and non-boolean values (including `null`) are rejected with 422. `event_type`/`modules` appear on every
event response (list, detail, create, update, duplicate, status change, search). They are independent of
`status` (workflow) and `lifecycle_state` (time-based).

## Event Types are backend configuration, not a hardcoded enum

`GET /api/v1/event-types` is the source of truth Web/mobile must call to render the Event Type picker —
never a client-side hardcoded list. Each Event Type is:

| Field | Meaning |
|---|---|
| `id` | stable UUID |
| `key` | the identifier `event_type` stores — lowercase snake_case, 1-30 chars (`^[a-z][a-z0-9_]{0,29}$`), **immutable once created** |
| `name` | display label |
| `active` | inactive types cannot be newly selected, but never invalidate events that already use them |
| `default_modules` | applied once, when this type is selected for a **new** event |
| `allowed_modules` | which modules an event of this type may enable at all |
| `required_modules` | which modules an event of this type may never disable |

Each of `default_modules`/`allowed_modules`/`required_modules` is the same always-all-eight-keys boolean
shape as `modules` on an Event — no second module representation exists. Invariant, enforced on every
Event Type create/update: `required[k] ⟹ default[k] ⟹ allowed[k]` (a module can only be required if it
is also defaulted on, and can only be defaulted on — or required — if it is also allowed).

### Event Type CRUD (`/api/v1/event-types`)

| Method | Path | Auth |
|---|---|---|
| `GET` | `/` | public; `active` types only unless `?include_inactive=true` |
| `GET` | `/{id}` | public |
| `POST` | `/` | Super Admin only |
| `PATCH` | `/{id}` | Super Admin only; `key` cannot be changed |
| `DELETE` | `/{id}` | Super Admin only; `409` if any Event references the key — deactivate (`active=false`) instead |

"Super Admin only" is the same strict gate Event Form Configuration writes use
(`require_form_configuration_super_admin`): an active platform Super Admin, never a tenant `admin`/
`provider`, even though those roles can configure individual Events. `allowed_modules`/`required_modules`
default to "everything allowed" / "nothing required" when omitted on create, so a minimal Event Type needs
only a `key`, `name`, and `default_modules`.

**Editing an Event Type's module rules never touches an event already created under it** — an Event's
`modules` is computed once, at write time, and persisted; it is never recalculated from the Event Type on
read. This is the same "compute once" rule Phase 2.2 has always followed, now proven against a real,
editable database row instead of a hardcoded Python dict.

## Create

- `event_type` + no `modules` → that type's defaults. `422` ("Unknown or inactive event type: …") if the
  key does not name an existing, active Event Type.
- `event_type` + `modules` → the defaults with the given modules applied on top (`{"meals": false}` turns
  meals off) — `422` if an override is not in the type's `allowed_modules`, or disables a
  `required_modules` entry.
- `modules` only → the event's behaviour-based modules (see Legacy) with the given modules on top;
  `event_type` stays unset.
- neither → the event stays "legacy" (nothing stored). Clients that do not know about this feature are
  unaffected.

A paid event (paid pricing, a price, or priced ticket types) always keeps `tickets: true` when a type's
defaults are applied — **unless** that type's `allowed_modules.tickets` is `false`, in which case this is a
genuine configuration conflict and the request is rejected (`422`, "… does not allow tickets, but this
event is paid"), never silently overridden.

## Update (PUT; partial semantics)

`null` or omitted means **no change**, so an update that does not mention configuration never resets it, and
a client that serializes absent values as `null` cannot wipe it. There is no "clear" operation.

- `modules` given → **merged** over the current configuration; modules you do not send keep their value.
  `422` if a change is not allowed, or disables a required module, for the event's (possibly just-changed)
  Event Type.
- `event_type` given → stored. It **never rewrites an already-configured `modules`** — but if those
  persisted modules would be **incompatible** with the new type's `allowed_modules`/`required_modules`
  (checked against the request's own overrides applied first), the whole update is rejected (`422`, naming
  every incompatible module) rather than silently creating an invalid combination. If they remain
  compatible, the type changes and `modules` is left completely untouched. Only an event that was never
  configured gets the new type's defaults, and only when the type really changes. Sending back the
  `"other"` read from a legacy event changes nothing.

## Validation

Two kinds of 422, both checked before anything is written:

1. **Generic, Event-Type-independent** (unchanged since Phase 2.2): `online_meeting: true` needs
   `delivery_mode` `online` or `hybrid` (on update, the resulting delivery mode); `tickets: false` is
   refused for a paid event; disabling `sessions` never deletes session data.
2. **Event-Type-specific** (new): an override enabling a module the type's `allowed_modules` forbids, or
   disabling one its `required_modules` demands.

Values that come from a type default are never rejected by either kind — only what the caller explicitly
asked for.

## Legacy events (created before Phase 2.2)

Unchanged. Both columns are NULL and are **not backfilled**. On read they are resolved from what the event
does today: `registration` and `check_in` are on; `tickets` if the event is paid or has ticket types;
`sessions` if it has sessions; `online_meeting` if `delivery_mode` is online/hybrid or a meeting link/
provider is set; `custom_questions` if it has registration questions, custom values, or is pinned to an
Event Form version; `meals` and `accommodation` are off. Nothing an existing event already uses is
disabled, and nothing is written by a read. Persisted values always win over this derivation. A stored
`event_type` that is a well-formed string but no longer names a known Event Type (deactivated, or deleted
once unused) is returned exactly as stored — resolution never re-validates it against the current table.

## Database

- `events.event_type` `VARCHAR(30)` NULL, `events.modules` `JSONB` NULL — **unchanged since Phase 2.2**
  (Alembic `adae1a2909cc`). No new column, no foreign key: `event_type` is matched to `event_types.key` by
  value only, so an Event Type can be edited or safely deleted without any migration touching `events`.
- `event_types` (Alembic `070237a7c1cf`, chained onto the Phase 2.7 head `119f4bd408a4`): `id`, `key`
  (unique, indexed), `name`, `active`, `default_modules`/`allowed_modules`/`required_modules` (JSONB),
  `created_at`, `updated_at`. Seeded with the 7 types this table replaces (`conference`, `workshop`,
  `marathon`, `camp`, `private_function`, `webinar`, `other`), `default_modules` copied byte-for-byte from
  the removed hardcoded table, `allowed_modules` fully permissive and `required_modules` fully empty for
  all 7 — behaviourally identical to before this migration.
