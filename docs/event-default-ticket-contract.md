# Event default ticket (Base Price) contract

`Event.price` (the **Base Price**) and configured `ticket_types` (Regular, VIP, ...) are **independent purchasable
options**. Clients never generate ticket ids.

## Response

- `default_ticket_type_id` — backend-generated UUID of the Base Price option, or `null` when the Event cannot sell it.
- `ticket_types` — only the configured tickets, with their own ids/prices. The default ticket is **never** written
  into this list.
- Purchasable options = `[default ticket (if id is non-null)] + ticket_types`.
  The default ticket is sold at the Event's **current** `price` and `currency` (name "General admission").

## Eligibility (re-checked on every quote, checkout and walk-in)

1. `price` is a positive amount,
2. `currency` is a valid Event currency (Event currency is authoritative; mismatches are rejected),
3. Base Price is enabled in the Event's pinned Event Form version (no pinned version = legacy Event = enabled).

Configured Ticket Types have **no** effect on eligibility. Free Events never qualify.

## Resolution (`app/utils/event_default_ticket.resolve_ticket_option`, used by quote, checkout and walk-in)

| Requested `ticket_type_id` | Result |
|---|---|
| id of a configured ticket | that ticket (always wins) |
| this Event's stored default id and eligible | the default ticket |
| this Event's stored default id but not eligible | 404 `Ticket type not found` |
| anything else (unknown / stale / other Event's id) | 404 `Ticket type not found` |
| none | legacy single-price path (unchanged); walk-in still requires an id when tickets are configured |

Capacity: the default ticket has no quota of its own; it and every configured ticket share the Event capacity, which
is enforced under the Event row lock exactly as before. A configured ticket's own `capacity` limits only that ticket.

## Assignment

- New Events: the id is generated once at creation when eligible.
- An update that makes an Event eligible for the first time assigns one; an Event that was already eligible is never
  silently enabled. The id never changes once assigned, and is kept (but hidden/unsellable) while ineligible.
- Existing Events: no migration and no automatic backfill. Use `scripts/enable_default_ticket_type.py`
  (`--list-eligible` is read-only; then `--event-id ... [--dry-run]`).
- Orders and registrations keep their stored `ticket_type_id`, amount and currency; nothing here rewrites them.
