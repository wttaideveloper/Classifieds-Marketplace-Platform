# Event wishlist API

For the mobile and web developers. Base path `/api/v1/events`. Same pattern as the training wishlist. All three routes need a login: without a token they answer `401`.

| Method and path | What it does |
|---|---|
| `GET /events/my/wishlist` | the events I saved, newest first |
| `POST /events/{event_id}/wishlist` | save an event. No body needed |
| `DELETE /events/{event_id}/wishlist` | remove it |

Also: `GET /events/{id}` and every item of `GET /events` have `is_wishlisted` (`true` when the signed-in caller saved the event, always `false` when anonymous), so the heart shows the right state without loading the whole list.

## Save: `POST /events/{event_id}/wishlist`

`201` the first time, **`200` with the same body when it was already saved** (saving twice is never an error). `404 {"detail": "Event not found"}` when the event does not exist or the caller cannot see it (for example a draft). A published event can be saved by anyone signed in.

```json
{
  "id": "6c1f…", "event_id": "b0e2…", "title": "Health Harmony Summit",
  "primary_image": "https://cdn.example.com/events/harmony.jpg",
  "price": "100.00", "currency": "USD", "pricing_type": "paid",
  "start_at": "2026-10-21T07:00:00Z", "end_at": "2026-10-21T16:00:00Z", "time_zone": "Asia/Kolkata",
  "delivery_mode": "hybrid", "enterprise_name": "Tester Shop Private Limited", "location": "Chennai",
  "average_rating": 4.0, "reviews_count": 1, "added_at": "2026-10-27T08:30:00Z", "status": "published"
}
```

## List: `GET /events/my/wishlist`

`200` with an array of the same objects, newest saved first, `[]` when there are none.

- Only events the caller can still see are listed: published events, and events they registered for (so a cancelled or completed event they hold a ticket for stays). An event that is unpublished or deleted drops out of the list and **comes back if it is published again**; the saved entry is not lost.
- Each person sees only their own list.

## Remove: `DELETE /events/{event_id}/wishlist`

Always `200 {"ok": true, "is_wishlisted": false}`, also when the event was not saved.

## Field notes

- `start_at` and `end_at` are **UTC instants** (written with `Z`). An event's stored time is its own wall-clock time in `time_zone` (the rest of the events API returns that naive value). Here it is converted for you; show it in `time_zone` if you want the event's own clock. Both are `null` when the event has no date.
- `price` is the text stored on the event (`"100.00"`), `null` when none. Use `pricing_type` (`free` or `paid`) to decide whether to show a price.
- `location` is the venue's city, else the venue's name, else the business location's city or name. `null` for an online event with none.
- `average_rating` is a number (`4.0`), not text, like everywhere else in the events API. It is `null` when the event has no approved reviews: show "No ratings yet", not 0.
- `status` is the event's own status. `added_at` is when it was saved.
- Migration: `e9c3a7b1d5f2` (table `event_wishlist_items`, one row per user and event).
