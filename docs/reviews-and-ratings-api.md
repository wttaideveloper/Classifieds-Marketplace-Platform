# Reviews & Ratings API

Base URL: https://chat.wisdomtooth.tech/api/v1
Swagger: https://chat.wisdomtooth.tech/docs

This document has three parts: the list of APIs, the details of each API, and a guide for the web and mobile developers. For how the feature is supposed to work for customers, Enterprise Owners and Super Admins, see `reviews-and-ratings-spec.md`.


## 1. API list

### Training
| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | /trainings/{training_id}/reviews | none | list reviews and average |
| POST | /trainings/{training_id}/reviews | login, enrolled | add or update own review |

### Product
| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | /products/{product_id}/reviews | none | list approved reviews and average |
| POST | /products/{product_id}/reviews | login | add or update own review |
| DELETE | /products/{product_id}/reviews/{review_id} | login | delete own review (admin, provider, super admin can delete any) |
| PATCH | /products/{product_id}/reviews/{review_id}/moderate | Enterprise Admin, Super Admin | approve, reject or reset a review |

### Service
| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | /services/{service_id}/reviews | none | list approved reviews and average |
| POST | /services/{service_id}/reviews | login | add or update own review |
| DELETE | /services/{service_id}/reviews/{review_id} | login | delete own review (admin, provider, super admin can delete any) |
| PATCH | /services/{service_id}/reviews/{review_id}/moderate | Enterprise Admin, Super Admin | approve, reject or reset a review |

### Program
| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | /programs/{program_id}/reviews | login | list reviews and average |
| POST | /programs/{program_id}/reviews | login, enrolled | add a review |

### Event
| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | /events/{event_id}/reviews | login, registered | add a review (stored as pending) |
| POST | /events/{event_id}/feedback | login | submit feedback |
| GET | /events/{event_id}/feedback | event manager | read feedback |
| PATCH | /events/{event_id}/reviews/{review_id}/moderate | owning Enterprise Admin | approve, reject or reset a review |

### Rating summaries that already come with other APIs
| Path | Fields |
|---|---|
| GET /trainings/ | average_rating, reviews_count on each item |
| GET /trainings/my/wishlist | average_rating, reviews_count on each item |
| GET /trainings/{id} | average_rating, reviews_count, reviews (latest 50) |
| GET /trainings/reports/summary | average_rating across the owner's trainings (admin, provider) |
| GET /events/reports/summary | average_rating for the dashboard (admin, provider) |
| GET /events/{event_id}/reports?type=feedback | total_feedbacks, total_reviews, average_rating (event manager) |

### Not available yet
Listing pending reviews for moderation, reading event reviews, "my review", replies, helpful votes, photos, star breakdown, reporting a review, review notifications.


## 2. General notes

- Auth header: `Authorization: Bearer <token>`. The web session cookie also works.
- rating is an integer 1-5, otherwise 422.
- Timestamps are UTC with no "Z" at the end (example: 2026-10-07T09:15:30.123456).
- Training, product and service allow one review per user per item. Posting again updates the old one and still returns 201.
- List endpoints are not paginated. They return everything, newest first.
- Error body is `{"detail": "..."}`. A 422 has a list in detail: `{"detail": [{"loc": ["body","rating"], "msg": "...", "type": "..."}]}`.

Status codes: 401 not logged in, 403 not allowed, 404 not found, 422 bad input.


## 3. API details

### Training

#### POST /trainings/{training_id}/reviews
Login required. Only learners enrolled in the training can post (enrolment status enrolled / active / completed / approved). Pending, rejected, cancelled and waitlisted get 403 "Verified reviews only — must be enrolled to review".

Request
```json
{ "rating": 4, "comment": "Clear and practical." }
```
comment is optional and has no max length on the server. participant_email is no longer needed, the logged in user is used.

Response 201
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
No moderation for training, the review is public immediately.

#### GET /trainings/{training_id}/reviews
Public.

Response 200
```json
{
  "reviews": [ { same fields as above } ],
  "average_rating": 4.0,
  "count": 1
}
```
average_rating is 0 when there are no reviews.

Please don't show participant_email in the UI, use participant_name. It can be null, use "Learner" in that case.

GET /trainings/{id} also returns a reviews array (latest 50) with id, training_id, participant_email, rating, comment, created_at. It has no participant_name or verified, so use the list endpoint for the reviews screen.


### Product and Service

Same for both. Replace `products/{product_id}` with `services/{service_id}`. The only difference is is_verified_purchase: for products it is true if the user has a confirmed order with that product, for services it is always false. Service responses have service_id instead of product_id.

#### POST /products/{product_id}/reviews
Login required, any user.

Request
```json
{ "rating": 5, "comment": "Great quality, fast delivery." }
```
comment optional, max 2000 characters.

Response 201
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
- a new review is "pending" and is not in the public list until approved
- editing keeps the current moderation_status
- reviewer_name falls back to the email if the user has no name, so hide it when it contains "@"

#### GET /products/{product_id}/reviews
Public. Returns approved reviews only.

Response 200
```json
{
  "reviews": [ { same fields as above, moderation_status "approved" } ],
  "average_rating": 4.6,
  "total": 12
}
```
average_rating is null when there are no approved reviews (show "No ratings yet").

#### DELETE /products/{product_id}/reviews/{review_id}
Login required. The author can delete their own review. Admin, provider and super admin can delete any.
200 `{"message": "Review deleted"}`. Someone else's review as a normal user: 403 "You can only delete your own review".

#### PATCH /products/{product_id}/reviews/{review_id}/moderate
Enterprise Admin or Super Admin only. Providers get 403.

Request
```json
{ "action": "approved" }
```
action is approved, rejected or pending. Returns the updated review. Any other value is 400.

#### Things to know
- There is no endpoint to get "my review". Keep the one returned by POST, or look for your user_id in the public list (only works after approval).
- There is no endpoint to list pending reviews, so moderate can only be called if you already know the review id.
- The `rating`, `reviews` and `reviews_count` fields inside product/service/enterprise list and detail responses are not connected to these reviews (rating is always 0). Use the GET reviews endpoint instead.


### Program

#### POST /programs/{program_id}/reviews
Login required.

Request
```json
{ "rating": 5, "comment": "Life-changing.", "participant_email": "learner@example.com" }
```
participant_email is required and must belong to an enrolment on the program, otherwise 400 "Verified reviews only — must be enrolled to review". The server does not check it against the logged in user, so send the user's own email.

Response 201
```json
{
  "id": "...", "program_id": "...", "rating": 5, "comment": "Life-changing.",
  "participant_email": "learner@example.com", "verified": true, "created_at": "..."
}
```
Every POST creates a new review (no update). Disable the button after a successful submit.

#### GET /programs/{program_id}/reviews
Login required (unlike training).

Response 200
```json
{
  "reviews": [ { "id": "...", "rating": 5, "comment": "...", "participant_email": "...", "created_at": "..." } ],
  "average_rating": 5.0,
  "count": 1
}
```
No moderation for program reviews.


### Event

Events have feedback and reviews, both stored in the same table.

#### POST /events/{event_id}/reviews
Login required.

Request
```json
{ "participant_email": "learner@example.com", "rating": 5, "comment": "Loved it" }
```
- participant_email is required (400 if missing)
- must belong to a registration with status confirmed or attended, otherwise 403 "Only registered participants can submit verified reviews"
- rating is not validated on the server and duplicates are not checked, so validate 1-5 in the app
- the email is not checked against the logged in user

Response 201
```json
{
  "id": "...", "event_id": "...", "participant_email": "learner@example.com",
  "form_id": null, "answers": null, "rating": "5", "comment": "Loved it",
  "is_review": true, "moderation_status": "pending", "created_at": "2026-10-07T09:15:30.123456"
}
```
Note rating is a string here.

#### POST /events/{event_id}/feedback
Login required. Free-form feedback, body can have participant_email, form_id, answers, rating, comment.

#### GET /events/{event_id}/feedback
Event managers only.

#### PATCH /events/{event_id}/reviews/{review_id}/moderate
Owning Enterprise Admin only. A Super Admin is refused at the moment.

Request
```json
{ "action": "approved" }
```

There is no endpoint to read event reviews, so there is nothing to display yet. Only the submit form can be built.


## 4. Guide for frontend developers

### 4.1 Both web and mobile

Which API for which screen

| Screen | Call |
|---|---|
| Training card (list, wishlist) | average_rating and reviews_count already in the response |
| Training detail, reviews section | GET /trainings/{id}/reviews |
| Product or service detail, reviews section | GET /products/{id}/reviews (or services) |
| Program detail, reviews section | GET /programs/{id}/reviews (signed in users only) |
| Event detail | no review list yet, submit form only |
| Product or service card | do not use rating, reviews or reviews_count from the response, they are always empty or 0 |

Rules to apply in every client
- Send rating as a number, not a string.
- Limit the comment box to 2000 characters. Training, program and event do not enforce it on the server.
- Never show an email address. For training use participant_name and fall back to "Learner". For product and service hide reviewer_name when it contains "@".
- Convert timestamps to local time. Treat the string as UTC: add "Z" before parsing.
- Show "No ratings yet" when average_rating is null (product, service). For training and program, show the empty state when count is 0.
- Don't depend on 201 to know if a review is new or edited.

When the user may write a review

| Module | Show the "Write a review" button when |
|---|---|
| Training | the user's enrolment_status on the training detail is enrolled (or active / completed / approved) |
| Product | the user is signed in |
| Service | the user is signed in |
| Program | the user is signed in and enrolled in the program |
| Event | the user is signed in and has a confirmed or attended registration |

After the submit
- Training: public at once, reload the list.
- Product and service: if moderation_status is "pending" show "Thanks, your review will appear once it has been approved". Keep the returned review locally so the user can see it and edit it.
- Program: public at once, disable the button, reload the list.
- Event: show a thank you message. The review is not visible anywhere.

Error messages to map

| Status | What to show |
|---|---|
| 401 | send the user to sign in, then back to the item |
| 403 on submit | "You need to be enrolled (or registered) to review this". Server text can be shown as is |
| 404 | "This item is no longer available" |
| 422 | "Choose 1 to 5 stars" or "Comment is too long" |

Not available yet, so don't design for it: pagination, sorting, filters, "my review", replies, helpful votes, photos, star breakdown.

### 4.2 Web

- Auth: if the app uses the session cookie, send requests with `credentials: "include"`. If it uses the access token, send the Bearer header. Public GET calls need neither.
- To find the signed in user's id (for matching user_id in a product or service list): GET /api/v1/auth/session, then data.id.

Example: post a review
```js
const res = await fetch(`${API}/products/${productId}/reviews`, {
  method: "POST",
  headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
  body: JSON.stringify({ rating: 5, comment })
});
if (!res.ok) {
  const err = await res.json();
  // err.detail is a string, or a list when the status is 422
}
const review = await res.json();
```

Example: read a UTC timestamp
```js
const date = new Date(review.created_at + "Z");
date.toLocaleDateString();
```

- Render at most 20 to 50 reviews and add a "Show more" button on the client side until the backend has pagination.
- After a successful POST, re-fetch the list for training and program. For product and service do not re-fetch to find the new review, it is still pending and will not be in the list.
- Comments can contain line breaks. Render as text, never as HTML.

### 4.3 Mobile

- Auth: Bearer token on every call except the public GETs. If the token has expired (401), refresh it with the normal login flow and retry once.
- Store the review returned by POST (product and service) in local storage keyed by item id. There is no "my review" call, so this is how the edit screen and the "awaiting approval" label survive an app restart. Clear it if the user signs out.
- Parse the timestamps as UTC. On Android, `LocalDateTime.parse(value).atOffset(ZoneOffset.UTC)`. On iOS, an ISO8601 formatter with fractional seconds and the time zone set to UTC.
- The full review list is returned in one response, so use a paged list view on the device and show the first 20 items.
- Training cards can show the rating straight from the list response. For product and service cards, don't show a rating until the detail screen has loaded the reviews, or leave it out of the card.
- Disable the submit button while the request is running, and keep the typed text if the request fails.
- Navigation from push or in-app notifications is not part of reviews yet. There are no review notifications.

### 4.4 Known gaps (backend)

1. No pagination, sorting or filters on any review list.
2. Training list returns reviewer emails publicly.
3. No list of pending reviews for product, service and event, so moderation can't be used in practice.
4. No public list or rating summary for events. Event rating is not validated.
5. Event and program reviews trust the email sent in the body.
6. Product and service moderate/delete don't check that the review belongs to the caller's business. Editing does not reset the review to pending.
7. Product, service and enterprise responses have rating 0 and reviews/reviews_count from an old unlinked store.
8. Not available: "my review" endpoint, helpful votes, replies, photos, star breakdown, report abuse.
