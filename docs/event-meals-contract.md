# Event meals: backend contract (Phase 2.6)

A small, configurable meal capability: an event has a list of **meal options**, and a registration records **which
options the attendee selected**. Nothing else: no meal capacity, prices, vendors, kitchen or inventory, and no change
to orders, payment, checkout, tickets, waitlist or capacity.

Registration, checkout, tickets/QR, waitlist, walk-in, event check-in, session check-in, attendee management, the
dashboard, meeting links, `status`, `lifecycle_state`, `event_type` and `modules` behave as before; every new field is
optional and additive.

## Data model (no new table)

| Where | Column | Meaning |
|---|---|---|
| `events.meals` | JSONB, NULL | `{"options": [{id, name, description?, date?, active}]}` |
| `event_registrations.meal_selections` | JSONB, NULL | a JSON list of the selected option ids, in the event's option order |

NULL means "no options" / "none selected": every existing event and registration (Alembic `d3a5cd7d0a58`, below).

### `modules.meals` is the only switch

Whether meals are on is `modules.meals` (Phase 2.2) and nowhere else. The column stores **options only**; there is no
second "enabled" value to drift. `meals.enabled` in responses is a read-only mirror of `modules.meals`. It is accepted on
input only so an edit form can echo an event back: if sent it must equal `modules.meals` or the request is `422`.
Turning `modules.meals` off keeps the options (and every registration's selections); turning it on again restores them.
Legacy events (`modules` NULL) resolve to meals **off** with no options, exactly as before; nothing is ever written by a read.

## Configuration (Event create / update)

```json
{ "event_type": "conference",
  "meals": { "options": [ {"id": "breakfast-day-1", "name": "Breakfast", "date": "2027-01-10"},
                          {"name": "Lunch"}, {"name": "Dinner", "description": "Served 7-9pm"} ] } }
```

`EventCreate.meals` / `EventUpdate.meals` take `{enabled?, options?}`. An option is `id?` (letters, digits, `_`, `-`; 1-64
chars, starts with a letter or digit), `name` (1-100, not blank), `description?` (max 300), `date?` (`YYYY-MM-DD`) and
`active` (default true). Unknown fields anywhere are `422`, so no price, capacity, vendor or tenant field can be sent.
`null` as an option, a non-list `options`, more than 50 options, duplicate ids (case-insensitive) or a duplicate active
name+date are rejected. Errors are `422` with a message; a failed request changes nothing.

* **Create:** options need meals on (from the `event_type` default or an explicit `modules.meals: true`). If meals are off the
  request is rejected (`422`, "Meals are disabled ... set modules.meals to true") instead of silently enabling the module.
  With meals on and no options, nothing is stored.
* **Update:** partial. `meals` null/omitted, or without `options`, is **no change**. An unrelated update, or one that only changes
  `modules`, never touches the options. Options are judged against the modules *as they will be after the same request*, so
  `modules.meals: true` and `meals.options` can be sent together. Changing options while meals are off is `422`, but sending
  back exactly the stored options is a no-op, so an edit form that round-trips a disabled event keeps working.

### Stable ids

An option's identity is its `id`, never its position; registrations reference ids.

* An option you send **with** an id keeps it (rename, re-date, reorder are all fine).
* An option **without** an id reuses the id of the existing option with the same name (case-insensitive) and date; only a
  genuinely new option gets a new id (a uuid4). A client that does not track ids therefore never orphans selections.
* Ids are never regenerated on update, and an id is unique within its event only (a duplicated event keeps its ids).

### Removing an option = retiring it

`options` is the desired set of **active** options. An existing option that an update no longer lists is **not deleted**: it
stays with `active: false` (after the listed ones), so historical selections keep resolving and reports stay truthful. A retired
option cannot be newly selected; an attendee who already holds it keeps it (they can save the rest of their list unchanged). Re-listing
it (same id, or same name and date) reactivates it. `options: []` retires everything. Storage is capped at 100 options including
retired ones. Nothing is written during a `GET`.

## Selections

`meal_selections` is a list of option ids. It appears (optionally) on:

| Where | Notes |
|---|---|
| online registration `POST /events/{id}/registrations` | the registrant only, not group members. Free-event path; paid events register through checkout, which is **unchanged** and has no meal field, so paid attendees choose with the endpoint below |
| walk-in `POST /events/{id}/walk-in` | the exact same validator; part of the same transaction |
| `PATCH /events/{id}/registrations/{reg_id}/meals` | replace the selection; `[]` clears it |

**One validator** (`validated_selections`) serves all three. `null` or `[]` means "none" and is always accepted, so clients may
always send the field. A non-empty selection requires: meals on for the event; every id an option of THIS event (the event's
configuration is the source of truth: `lunch` is refused if no lunch is configured); no duplicates; and only active options
(except ids the registration already holds). Malformed input (not a list, null/non-string items, unsafe ids, more than 100) is
`422` from the schema; unknown / retired / disabled is `422` from the validator. Stored normalised: unique, in the event's
option order. Registration without selections is unchanged.

### `PATCH /events/{event_id}/registrations/{reg_id}/meals`

Body `{"meal_selections": ["..."]}` (no other field; ownership and identity fields are `422`). Response: the registration's
selections with names.

| Caller | Result |
|---|---|
| the participant (their email matches the registration's, case-insensitively and trimmed; the same identity rule as cancel/QR) | allowed |
| owning tenant `admin` / `provider`, active platform `super_admin` | allowed (Phase 2.1 ownership) |
| another customer, foreign tenant staff, inactive super admin | `403` |
| no token | `401` |
| registration of another event, unknown registration / event | `404` |

A registration id alone grants nothing. The registration must be active (`confirmed`/`attended`) and the event not
`cancelled`/`completed`/`archived`/`suspended` (`400`). Payment, capacity, the waitlist and orders are never touched. An
**organizer's** change is audited (`meal_selection_update`, before/after, operator); a participant changing their own
preference is not (no audit noise). Commit and audit row are one transaction.

## Attendee management, export, dashboard

* **Attendee list / detail / walk-in response:** `meal_selections: [{meal_id, name, active}]` in option order (retired options
  stay listed with `active: false`; an id the configuration does not know is shown with `name: null`). No extra query per attendee.
* **CSV export:** a final `meals` column, names joined with `; ` (`Breakfast; Lunch`); a cell that starts with `=`, `+`, `-` or `@`
  is neutralised like every other text cell. All earlier columns are unchanged.
* **Dashboard:** `meals: [{meal_id, name, selected_count, active}]`, one row per option of an event with meals ON (empty otherwise).
  Counted from **active registrations only** (`confirmed` + `attended`), so a cancelled registration, a refund that cancelled it
  (approval does), or a no-show does not count. Retired options appear only while at least one active registration still holds
  them. It is one grouped SQL statement: the JSON array is expanded in the database (`jsonb_array_elements_text` on PostgreSQL,
  `json_each` on SQLite) and grouped there; registrations are never loaded into Python. Every other dashboard figure is unchanged.

## Security

Organizer configuration goes through the existing Event create/update routes and their gates (create: `admin`/`provider`; update:
the owning `admin`/`provider`, Phase 2.1 ownership). Customers, foreign tenants, inactive super admins and anonymous callers are
denied. **The existing update route does not admit the platform super admin** (a Phase 2.1 role gate that this phase does not
change), so a super admin cannot edit meal options; they can use the registration meal endpoint above. The tenant/enterprise of an
event always come from the authenticated session, never the body; `meals` cannot carry any ownership field.

## Audit

Event create and update audit rows already record the changed configuration, so `meals` is captured naturally: `create` has
`after.meals`; an update that changes options has `before.meals` / `after.meals`; an unrelated update or a modules-only update does
not mention `meals`. Organizer meal updates on a registration are audited as above. Everything is staged and committed with the change,
so a failed request leaves no orphan audit row.

## Database

Two nullable JSONB columns: `events.meals` and `event_registrations.meal_selections` (Alembic `d3a5cd7d0a58`, parent `fe61c574e6f5`, the
Phase 2.5 head; still four heads, no merge). No default, no backfill, no index, no table.

## Not in this phase

Meal capacity or per-meal limits, meal pricing, meal orders, vendors/catering, kitchen or inventory, dietary rules, per-session meals,
group members' individual meals, meal selection in the paid checkout, accommodation.
