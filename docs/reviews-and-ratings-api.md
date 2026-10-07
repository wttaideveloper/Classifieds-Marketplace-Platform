# Reviews & Ratings — Backend API Guide

For the **web** and **mobile** frontend developers. Everything below describes what the backend does **today**, taken
from the code; the last section lists what does *not* exist yet so you don't build against it.

Base URL: `https://chat.wisdomtooth.tech/api/v1` · Swagger: `/docs`

## 1. At a glance

| Module | Submit / edit | List (public?) | Delete | Moderation | Who may review |
|---|---|---|---|---|---|
| **Training** | `POST /trainings/{id}/reviews` | `GET /trainings/{id}/reviews` — public | — | none (visible immediately) | learner with an active enrolment |
| **Product** | `POST /products/{id}/reviews` | `GET /products/{id}/reviews` — public, approved only | `DELETE …/{review_id}` | `PATCH …/{review_id}/moderate` | any logged-in user |
| **Service** | `POST /services/{id}/reviews` | `GET /services/{id}/reviews` — public, approved only | `DELETE …/{review_id}` | `PATCH …/{review_id}/moderate` | any logged-in user |
| **Program** | `POST /programs/{id}/reviews` | `GET /programs/{id}/reviews` — **login required** | — | none | enrolled participant |
| **Event** | `POST /events/{id}/reviews` | **none** (no public list yet) | — | `PATCH …/{review_id}/moderate` | registered participant |

Products and Services behave the same way; Training, Program and Event each differ — read the module you need.

## 2. Conventions (all modules)

- **Auth:** `Authorization: Bearer <access token>` (the web session cookie also works). A public endpoint needs no token.
- **Rating:** an integer **1–5**. Anything else → `422`. Responses always return it as a number.
- **Averages:** Training/Program return a number rounded to 2 decimals and `0` when there are no reviews. Product/Service
  return `null` when there are none — show "No ratings yet", not `0.0`.
- **Timestamps:** ISO 8601 strings **without** a timezone suffix, e.g. `"2026-10-07T09:15:30.123456"`. They are **UTC** — append
  `Z` (or parse as UTC) before converting to the viewer's local time.
- **Ids:** UUID strings.
- **One review per person per item** (Training, Product, Service): posting again **edits** your review. Both the first post
  and an edit return **`201`** — don't use the status code to tell them apart.
- **No pagination, sorting or filters** on any review list: every list returns all reviews at once, newest first (Training,
  Program, Product, Service). Cap what you render.
- **Errors:** `{"detail": "message"}`. Validation errors (`422`) are `{"detail": [{"loc": ["body","rating"], "msg": "...", "type": "..."}]}`.

Common status codes: `200/201` ok · `401` not logged in · `403` not allowed (message says why) · `404` item or review not found ·
`422` invalid body (rating outside 1–5, comment too long).

---

## 3. Training reviews

### Submit or edit — `POST /trainings/{training_id}/reviews`  (login required)

```json
{ "rating": 4, "comment": "Clear and practical." }
```

| Field | Type | Rules |
|---|---|---|
| `rating` | int | required, 1–5 |
| `comment` | string \| null | optional, no length limit enforced — cap it in the UI (suggest 2000) |
| `participant_email` | — | **ignore / don't send.** Deprecated: the reviewer is always the logged-in user |

**201** →
```json
{
  "id": "f76b59d5-f457-4caf-abcd-0feed5991806",
  "training_id": "94aa1aaa-2222-458a-80b3-f80d37137ec2",
  "rating": 4,
  "comment": "Clear and practical.",
  "participant_email": "learner@example.com",
  "participant_name": "Asha Rao",
  "verified": true,
  "created_at": "2026-10-07T09:15:30.123456"
}
```

Rules
- Only a learner with an **active enrolment** can review: enrolment status `enrolled` (also `active`, `completed`, `approved`).
  Pending, rejected, cancelled and waitlisted learners get `403` `"Verified reviews only — must be enrolled to review"`.
- A token with no email claim gets `403` `"Enrolled participants only"`.
- The review is **visible immediately** — Trainings have no moderation step.
- `verified` is always `true` (only enrolled learners can post).
- `participant_name` is the name on the learner's enrolment; it can be `null`, and for older enrolments it may be the email
  address. Prefer `participant_name`, fall back to "Learner"; **never show `participant_email`** in the UI (see §8).

### List — `GET /trainings/{training_id}/reviews`  (public)

```json
{
  "reviews": [
    {
      "id": "f76b59d5-f457-4caf-abcd-0feed5991806",
      "training_id": "94aa1aaa-2222-458a-80b3-f80d37137ec2",
      "rating": 4,
      "comment": "Clear and practical.",
      "participant_email": "learner@example.com",
      "participant_name": "Asha Rao",
      "verified": true,
      "created_at": "2026-10-07T09:15:30.123456"
    }
  ],
  "average_rating": 4.0,
  "count": 1
}
```

### Where Training rating summaries also appear

| API | Fields |
|---|---|
| `GET /trainings/` (each item) | `average_rating`, `reviews_count` |
| `GET /trainings/{id}` | `average_rating`, `reviews_count`, `reviews[]` — the **latest 50**, each `{id, training_id, participant_email, rating, comment, created_at}` (no `participant_name` / `verified`: call the list endpoint if you need them) |
| `GET /trainings/my/wishlist` (each item) | `average_rating`, `reviews_count` |

Use the list endpoint for the full review screen; use the summary fields for cards.

---

## 4. Product reviews  ·  5. Service reviews

Identical, except for one field: `is_verified_purchase` is computed for Products (the user has a confirmed order containing
that product) and is **always `false` for Services** (there is no booking record to verify against yet).

Paths: `/products/{product_id}/reviews[/{review_id}]` and `/services/{service_id}/reviews[/{review_id}]`.

### Submit or edit — `POST …/reviews`  (login required)

```json
{ "rating": 5, "comment": "Great quality, fast delivery." }
```

| Field | Type | Rules |
|---|---|---|
| `rating` | int | required, 1–5 |
| `comment` | string \| null | optional, **max 2000 characters** (`422` above that) |

**201** → the saved review:
```json
{
  "id": "0b6f3c1e-5d1a-4a8e-9a53-0d2f6d4a7c11",
  "product_id": "62078973-39ac-46e5-b867-6196935025ba",
  "user_id": "9d1f7a52-3c4e-4b86-a0d5-2e8c6f1b7a90",
  "reviewer_name": "Asha Rao",
  "rating": 5,
  "comment": "Great quality, fast delivery.",
  "is_verified_purchase": true,
  "moderation_status": "pending",
  "created_at": "2026-10-07T09:15:30.123456",
  "updated_at": "2026-10-07T09:15:30.123456"
}
```
(Service responses carry `service_id` instead of `product_id`.)

- A **new** review starts as `moderation_status: "pending"` and **does not appear in the public list** until an admin approves
  it. Show the author "Your review is awaiting approval".
- **Editing** your review keeps its current `moderation_status` (an approved review stays visible after an edit).
- `reviewer_name` is the user's display name; if the account has none it falls back to their email — don't render it blindly
  if it contains `@`.
- Unknown product/service → `404`. No login → `401`.

### List — `GET …/reviews`  (public, approved only)

```json
{
  "reviews": [ { "id": "…", "product_id": "…", "user_id": "…", "reviewer_name": "Asha Rao", "rating": 5,
                 "comment": "…", "is_verified_purchase": true, "moderation_status": "approved",
                 "created_at": "…", "updated_at": "…" } ],
  "average_rating": 4.6,
  "total": 12
}
```
`average_rating` is the mean of **approved** reviews, `null` when there are none; `total` is the number of approved reviews.

### Delete — `DELETE …/reviews/{review_id}`  (login required)

The review's author, or any admin / provider / super admin. → `200 {"message": "Review deleted"}`.
Someone else's review as an ordinary user → `403` `"You can only delete your own review"`. Unknown review → `404`.

### Moderate — `PATCH …/reviews/{review_id}/moderate`  (Enterprise Admin or Super Admin)

```json
{ "action": "approved" }
```
`action` is `approved`, `rejected` or `pending`; returns the updated review. Providers are read-only here (`403`
`"Providers are read-only; Enterprise Admin access required"`). Any other action → `400`.

### "My review" on a product/service page

There is **no "get my review" endpoint**. Two workable options: (a) keep the review returned by your own `POST` and show it
locally; (b) find it in the public list by `user_id === <your user id>` (`GET /api/v1/auth/session` → `data.id`) — but that only
finds it once it is **approved**. To edit, call `POST` again with the new text.

### ⚠ Do not use `rating`, `reviews` or `reviews_count` from Product / Service / Enterprise list or detail responses

Those responses contain `"rating": 0` (a fixed value) and a `reviews` / `reviews_count` pair read from an older, **separate**
store that the review endpoints above never write to. A review you just posted will **not** show up there. For real data call
`GET …/reviews`. (Fixing this is on the backend list in §8.)

---

## 6. Program reviews

### Submit — `POST /programs/{program_id}/reviews`  (login required)

```json
{ "rating": 5, "comment": "Life-changing.", "participant_email": "learner@example.com" }
```
| Field | Rules |
|---|---|
| `rating` | required, 1–5 |
| `comment` | optional |
| `participant_email` | **required** — must be the email of an existing enrolment on this program |

**201** → `{ "id", "program_id", "rating", "comment", "participant_email", "verified": true, "created_at" }`.
Not enrolled → **`400`** `"Verified reviews only — must be enrolled to review"`.

- Send the logged-in user's own email. The server does not check that it matches the login (see §8).
- **Not an upsert:** every `POST` creates **another** review. Disable the submit button after success; there is no edit.

### List — `GET /programs/{program_id}/reviews`  (**login required**, unlike Training)

```json
{
  "reviews": [ { "id": "…", "rating": 5, "comment": "Life-changing.", "participant_email": "learner@example.com", "created_at": "…" } ],
  "average_rating": 5.0,
  "count": 1
}
```
List items have no `program_id`, `verified` or reviewer name. No moderation.

---

## 7. Event reviews and feedback

Events have two separate things stored in the same table:

| | Feedback | Review |
|---|---|---|
| Submit | `POST /events/{id}/feedback` (login) | `POST /events/{id}/reviews` (login) |
| Body | any JSON: `participant_email?`, `form_id?`, `answers?`, `rating?`, `comment?` | `{ "participant_email": "...", "rating": 5, "comment": "..." }` |
| Read | `GET /events/{id}/feedback` — **event managers only** | **no endpoint** |
| Moderate | — | `PATCH /events/{id}/reviews/{review_id}/moderate` body `{ "action": "approved" \| "rejected" \| "pending" }` — owning Enterprise Admin |

Review rules: `participant_email` is **required** (`400` otherwise) and must belong to a registration with status `confirmed`
or `attended` (`403` `"Only registered participants can submit verified reviews"`). New reviews are stored `moderation_status: "pending"`.
There is no validation of `rating` (send an integer 1–5 yourself) and no duplicate check.

Saved record returned (`201`):
```json
{
  "id": "…", "event_id": "…", "participant_email": "learner@example.com", "form_id": null, "answers": null,
  "rating": "5", "comment": "Loved it", "is_review": true, "moderation_status": "pending", "created_at": "2026-10-07T09:15:30.123456"
}
```
Note `rating` comes back as a **string** here.

**Event reviews cannot be displayed to the public yet** — there is no list endpoint and the event detail carries no rating
summary. Build the submit form if needed, but not a review list, until the backend adds one.

---

## 8. Known gaps (what is *not* there / cautions)

Backend items — each is a small change if you need it; ask and we'll schedule.

| # | Area | Gap | What to do meanwhile |
|---|---|---|---|
| 1 | All | No pagination, sort or filter on review lists | Cap rendering client-side |
| 2 | Training | The public list returns every reviewer's **email** | Never display `participant_email`; use `participant_name` |
| 3 | Training | No edit-visibility rules, delete or report; no moderation | Edit = post again |
| 4 | Product / Service / Enterprise | `rating: 0` and `reviews` / `reviews_count` in list & detail come from an unlinked legacy store | Use `GET …/reviews` |
| 5 | Product / Service | Editing does not return a review to `pending`; staff delete/moderate is not limited to the item's own tenant | — |
| 6 | Event | No public list / summary; the email in the body is trusted (any logged-in user can post as any registered email); no rating validation or duplicate check | Send the user's own email; validate 1–5 in the app |
| 7 | Program | Email in the body is trusted; duplicates allowed; list needs login | Disable resubmit after success |
| 8 | All | No "my review" endpoint, helpful votes, owner replies, photos, rating distribution, or abuse reports | — |

## 9. Quick UI recipes

- **Star input:** integers 1–5; send the number, not a string.
- **After submit (Training / Product / Service):** treat `201` as "saved". Training → refresh the list. Product/Service → show
  "awaiting approval" if `moderation_status === "pending"`.
- **Card summary:** Training `average_rating` / `reviews_count` from the list or detail response; Product/Service call
  `GET …/reviews` (see the warning in §4–5).
- **Empty state:** Training/Program `count === 0`; Product/Service `total === 0` / `average_rating === null`.
- **Error copy:** `403` on submit means "you must be enrolled/registered to review"; show the server `detail` text.

## 10. Where this lives in the code

`app/api/v1/endpoints/{training,product,service,program,event}.py` (routes) ·
`app/services/{training,product,service,program,event}_service.py` (rules) ·
`app/schemas/{training,product,service,program}_schema.py` (response models) ·
models `TrainingReview`, `ProductReview`, `ServiceReview`, `ProgramReview`, `EventFeedback`.
