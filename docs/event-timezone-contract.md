# Event timestamp & timezone contract

Applies to `start_date`, `end_date`, `registration_cutoff`, `registration_open_at`, `registration_close_at`
(`Event` columns are naive `DateTime`).

## Storage and responses

- Stored value = **naive wall-clock time in `Event.time_zone`** (an IANA key such as `America/New_York`).
- API responses return exactly that naive value plus `time_zone`; they carry **no offset**. A client must read
  the value in `time_zone` (DST from the tz database), then convert to whatever zone it displays in.
- `lifecycle_state` and `registration_open` are computed server-side from the same reading
  (`app/utils/event_utils.py`: `resolve_naive_or_aware`, `validate_registration_window`,
  `get_event_lifecycle_state`). Registration, quote, checkout and waitlist all call
  `validate_registration_window`; staff walk-in intentionally ignores the customer window but not the lifecycle.
- `registration_close_at` and `registration_cutoff` have the same effect (registration is closed at the earlier
  of the two); the cutoff is a separate, optional organizer field.

## Writes (create and update)

| Input | Result |
|---|---|
| Naive timestamp (what Web sends: `2026-10-11T13:30:00`) | Stored as given; means wall-clock in `time_zone`. |
| Timestamp with offset or `Z` | Converted **once**, at the write boundary, to wall-clock in the event's zone; the instant is preserved. |
| `time_zone` omitted / null / blank on create | `Asia/Kolkata` (platform default, same as the column default). |
| `time_zone` null / blank on update | No change (never stores NULL). |
| `time_zone` not a valid IANA key | 422. |
| Update changes only `time_zone` | Existing wall-clock values are kept and re-read in the new zone (instants shift). Resend timestamps if the instants must stay fixed. |
| Update sends an offset timestamp and `time_zone` together | Converted using the **new** zone; with no `time_zone`, the stored one. |

## Legacy data

Stored rows are never rewritten by reads or by unrelated edits. A row with a NULL or invalid `time_zone` is
evaluated in UTC (invalid: with a logged warning) — unchanged historical behavior. A published row with an
*invalid* zone is rejected on edit until the owner sets a valid `time_zone` (the error names the field); a NULL
zone stays editable and is left NULL.
