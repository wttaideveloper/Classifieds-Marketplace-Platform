# Reviews & Ratings API

Base URL: https://chat.wisdomtooth.tech/api/v1
Swagger: https://chat.wisdomtooth.tech/docs

This document is for the web and mobile developers. Part 1 is the list of APIs, part 2 the rules that are common to all of them, part 3 the details of each API with samples, part 4 the notifications, and part 5 a guide for the frontend. How the feature works for the customer, the Enterprise Owner and the Super Admin is explained in `reviews-and-ratings-spec.md`.

Reviews exist for trainings, products, services and events. Courses are the same as trainings, so every `/trainings/...` path also works as `/courses/...`.

Deploy note for the backend team: this release adds three migrations (`e9a1c3b5d7f2` for training reviews, `f2b4d6a8c0e1` for event reviews and `a3c5e7f9b1d4` for the moderation history). Run them before the new code starts. Existing training reviews are marked approved by the migration, so they stay visible.


## 1. API list

Training and course

| Method | Path | Who can call | What it does |
|---|---|---|---|
| GET | /trainings/{training_id}/reviews | anyone | approved reviews, average, star count |
| POST | /trainings/{training_id}/reviews | logged in, enrolled | add or change own review |
| GET | /trainings/{training_id}/reviews/me | logged in | my own review, any status, or null |
| DELETE | /trainings/{training_id}/reviews/{review_id} | author, or staff of the owning business, or Super Admin | delete a review |
| PATCH | /trainings/{training_id}/reviews/{review_id}/moderate | Enterprise Admin, Super Admin | approve, reject or reset |

Product

| Method | Path | Who can call | What it does |
|---|---|---|---|
| GET | /products/{product_id}/reviews | anyone | approved reviews, average, star count |
| POST | /products/{product_id}/reviews | logged in | add or change own review |
| GET | /products/{product_id}/reviews/me | logged in | my own review, any status, or null |
| DELETE | /products/{product_id}/reviews/{review_id} | author, or staff of the owning business, or Super Admin | delete a review |
| PATCH | /products/{product_id}/reviews/{review_id}/moderate | Enterprise Admin, Super Admin | approve, reject or reset |

Service (same as product)

| Method | Path | Who can call | What it does |
|---|---|---|---|
| GET | /services/{service_id}/reviews | anyone | approved reviews, average, star count |
| POST | /services/{service_id}/reviews | logged in | add or change own review |
| GET | /services/{service_id}/reviews/me | logged in | my own review, any status, or null |
| DELETE | /services/{service_id}/reviews/{review_id} | author, or staff of the owning business, or Super Admin | delete a review |
| PATCH | /services/{service_id}/reviews/{review_id}/moderate | Enterprise Admin, Super Admin | approve, reject or reset |

Event

| Method | Path | Who can call | What it does |
|---|---|---|---|
| GET | /events/{event_id}/reviews | anyone | approved reviews, average, star count. Published events only |
| POST | /events/{event_id}/reviews | logged in, registered | add or change own review |
| GET | /events/{event_id}/reviews/me | logged in | my own review, any status, or null |
| DELETE | /events/{event_id}/reviews/{review_id} | author, or staff of the owning business, or Super Admin | delete a review |
| GET | /events/{event_id}/reviews/manage | Enterprise Admin, provider, Super Admin | every review of the event with status, counts per status, paging |
| PATCH | /events/{event_id}/reviews/{review_id}/moderate | Enterprise Admin, Super Admin | approve, reject or reset |
| POST | /events/{event_id}/feedback | logged in | send feedback |
| GET | /events/{event_id}/feedback | event manager | read feedback |

Moderation queue for all modules

| Method | Path | Who can call | What it does |
|---|---|---|---|
| GET | /reviews/manage | Enterprise Admin, provider (read only), Super Admin | every review the caller may moderate, any module and status, with counts and paging |
| PATCH | /reviews/{module}/{review_id}/moderate | Enterprise Admin, Super Admin | approve, reject or reset a review knowing only its module and id |
| DELETE | /reviews/{module}/{review_id} | author, or staff of the owning business, or Super Admin | delete a review knowing only its module and id |
| GET | /reviews/audit | Enterprise Admin, Super Admin | the moderation history: who approved, rejected, reset or deleted which review, and when |

Rating numbers that come with other APIs

| API | Fields |
|---|---|
| GET /trainings/, GET /trainings/my/wishlist | average_rating and reviews_count on every item |
| GET /trainings/{id} | average_rating, reviews_count and the latest 50 approved reviews |
| GET /products/ , GET /services/ , GET /search/... | rating and reviews_count on every item |
| GET /products/{id} , GET /services/{id} | rating, reviews_count and the latest 20 approved reviews |
| GET /trainings/reports/summary | average_rating across the owner's trainings (admin, provider) |
| GET /events/reports/summary | average_rating for the event dashboard (admin, provider) |
| GET /events/{event_id}/reports?type=feedback | in `data`: total_feedbacks, total_reviews, approved_reviews, average_rating (event manager) |

All of these count approved reviews only.

Not available yet: replies, helpful votes, photos, reporting a review.


## 2. Rules common to all APIs

- Send the token as `Authorization: Bearer <token>`. The web session cookie also works. The public GET calls need nothing.
- The rating is a whole number from 1 to 5. Anything else gives 422.
- The comment is optional and can have at most 2000 characters. More than that gives 422. Spaces around it are cut, and a comment with only spaces is saved as empty.
- A person has one review per item. Sending it again changes the old review. The server answers 201 both times, so you cannot tell new from edited by the status code.
- A new review is saved with `moderation_status: "pending"`. The public lists show approved reviews only.
- An Enterprise Admin of the business that owns the item, or a Super Admin, approves or rejects it. The `action` is `approved`, `rejected` or `pending`, and a review can be moved from any status to any other. A provider (staff) can read but gets 403 when trying to approve or reject.
- A business can only moderate reviews of its own items. Another business gets 403 "Not authorized for this tenant".
- Editing a review does not change its status. An approved review stays public after an edit.
- `average_rating` is the mean of the approved reviews, rounded to 2 decimals. It is null when there are none, and the app shows "No ratings yet", never 0.0. (On product and service cards the field is a plain number and is 0 when there are none, so check `reviews_count`.)
- `rating_distribution` is how many approved reviews gave each star: `{"5": 3, "4": 1, "3": 0, "2": 0, "1": 0}`.
- Nobody sees an email address in a public list. The author sees their own email in their own review, and the business's staff see it in the moderation lists.
- Dates are in UTC and have no "Z" at the end, for example `2026-10-07T09:15:30.123456`.
- The public lists return every approved review, newest first, unless you ask otherwise. Optional query: `sort` (`newest`, `oldest`, `highest`, `lowest`), `rating` (only that many stars), `with_comment=true`, and `page` / `page_size` (maximum 100, default 20 when paging). They change which reviews are listed. The average, the count and `rating_distribution` always cover all approved reviews. The answer has a `pagination` object (`total` is the number of reviews after the filters). The moderation lists are paged too.
- Errors come as `{"detail": "message"}`. For 422 from the body the detail is a list: `{"detail": [{"loc": ["body","rating"], "msg": "...", "type": "..."}]}`.

Status codes: 401 not logged in, 403 not allowed (the message says why), 404 item or review not found, 422 wrong input.


## 3. API details

### Training and course

#### POST /trainings/{training_id}/reviews

Only learners who are enrolled can review: the enrolment status must be enrolled, active, completed or approved. Learners who are pending, rejected, cancelled or on the waiting list get 403 with "Verified reviews only — must be enrolled to review". A token without an email gives 403 "Enrolled participants only".

Request
```json
{ "rating": 4, "comment": "Clear and practical." }
```
Do not send an email, the server uses the logged in user.

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
  "moderation_status": "pending",
  "created_at": "2026-10-07T09:15:30.123456",
  "updated_at": "2026-10-07T09:15:30.123456"
}
```
`verified` is always true, because only enrolled learners can post. The review is pending and is not in the public list until it is approved. The enrolment's name is `participant_name`; it is null if the enrolment has no real name (an email is never used as a name).

#### GET /trainings/{training_id}/reviews

Anyone can call it.

Response 200
```json
{
  "reviews": [
    {
      "id": "…", "training_id": "…", "rating": 4, "comment": "Clear and practical.",
      "participant_email": null, "participant_name": "Asha Rao", "verified": true,
      "moderation_status": "approved", "created_at": "…", "updated_at": "…"
    }
  ],
  "average_rating": 4.0,
  "count": 1,
  "rating_distribution": { "5": 0, "4": 1, "3": 0, "2": 0, "1": 0 },
  "pagination": { "total": 1, "page": 1, "page_size": 1, "total_pages": 1 }
}
```
Approved reviews only. `participant_email` is always null here. Show `participant_name`, and "Learner" when it is null. `average_rating` is null when there are none.

GET /trainings/{id} has the same approved reviews in a `reviews` list (latest 50) with id, training_id, participant_name, rating, comment and created_at, and the fields `average_rating` (null when none) and `reviews_count`.

#### GET /trainings/{training_id}/reviews/me

Login required. Returns the caller's own review in the same shape as the POST answer (with `moderation_status`), or `null` when they have not written one. Use it to fill the edit screen and to show "awaiting approval" after the app was closed.

#### DELETE /trainings/{training_id}/reviews/{review_id}

The author can delete their own review. Staff (admin or provider) of the business that owns the training can delete any review of it, and a Super Admin can delete any review. Response 200 `{"message": "Review deleted"}`. A learner deleting someone else's review gets 403 "You can only delete your own review". Staff of another business get 403 "Not authorized for this tenant". An unknown review, or one from another training, gives 404. When staff delete someone else's review it is written to the moderation history. Also available under `/courses/...`.

#### PATCH /trainings/{training_id}/reviews/{review_id}/moderate

Enterprise Admin of the business that owns the training, or a Super Admin.
```json
{ "action": "approved" }
```
Returns the review as in the POST answer. A provider gets 403 "Providers are read-only; Enterprise Admin access required". An admin of another business gets 403 "Not authorized for this tenant". A review that is not on this training gives 404. An action other than approved, rejected or pending gives 400. Also available as `/courses/{training_id}/reviews/{review_id}/moderate`.


### Product and service

The two are the same. For services use `/services/{service_id}/...` and the field is `service_id` instead of `product_id`. The only other difference is `is_verified_purchase`: for products it is true if the user has a confirmed order with that product, for services it is always false because we have no booking record.

#### POST /products/{product_id}/reviews

Any logged in user.

Request
```json
{ "rating": 5, "comment": "Great quality, fast delivery." }
```

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
A new review is pending. Editing keeps the status it had. `reviewer_name` is the user's display name. It is null when the account has no name; an email address is never returned as a name (older reviews that had one are returned with null too). Show "Customer" for null.

#### GET /products/{product_id}/reviews

Anyone can call it. It returns approved reviews only.
```json
{
  "reviews": [ { same fields as above, moderation_status "approved" } ],
  "average_rating": 4.6,
  "total": 12,
  "rating_distribution": { "5": 8, "4": 3, "3": 1, "2": 0, "1": 0 },
  "pagination": { "total": 12, "page": 1, "page_size": 12, "total_pages": 1 }
}
```
`average_rating` is null when there are no approved reviews.

#### GET /products/{product_id}/reviews/me

Login required. The caller's own review in the same shape as above (any status), or `null`.

#### DELETE /products/{product_id}/reviews/{review_id}

The author can delete their own review. Staff (admin or provider) of the business that owns the product can delete any review of it. A Super Admin can delete any review. Response 200 `{"message": "Review deleted"}`. A normal user deleting someone else's review gets 403 "You can only delete your own review". Staff of another business get 403 "Not authorized for this tenant". An unknown review gives 404.

#### PATCH /products/{product_id}/reviews/{review_id}/moderate

Enterprise Admin of the business that owns the product, or a Super Admin. A provider gets 403 "Providers are read-only; Enterprise Admin access required".
```json
{ "action": "approved" }
```
`action` is approved, rejected or pending. It returns the updated review. Any other value gives 400. The review must belong to the product in the URL, otherwise 404.

#### Rating on the cards and the detail page

- List and search items (`GET /products/`, `GET /services/`, `GET /search/products`, `GET /search/services`) have `rating` (a number, 0 when there are no approved reviews) and `reviews_count`.
- `GET /products/{id}` and `GET /services/{id}` have `rating`, `reviews_count` and `reviews`: the latest 20 approved reviews as `{ id, rating, comment, reviewer_name, created_at }`.
- The enterprise responses (`rating`, `reviews_count`) are not connected to these reviews.


### Event

Events keep two things in the same table: feedback (any form answers) and reviews (stars and a comment).

#### POST /events/{event_id}/reviews

Request
```json
{ "rating": 5, "comment": "Loved it" }
```
- The reviewer is the logged in user. An email in the body is ignored, so nobody can review as someone else.
- The user needs a registration with status confirmed or attended on this event. If not, 403 "Only registered participants can submit verified reviews". A token without an email also gets this.
- `rating` is required, 1 to 5. A number or a text such as "4" is accepted. Missing or anything else gives 422.
- `comment` is optional, at most 2000 characters.
- One review per person. Sending it again changes it and keeps its status.

Response 201
```json
{
  "id": "…",
  "event_id": "…",
  "participant_email": "learner@example.com",
  "form_id": null,
  "answers": null,
  "rating": "5",
  "comment": "Loved it",
  "is_review": true,
  "moderation_status": "pending",
  "created_at": "2026-10-07T09:15:30.123456"
}
```
The rating comes back as text here ("5"). The public list returns it as a number.

#### GET /events/{event_id}/reviews

Anyone can call it, no login. It returns the approved reviews of the event, newest first, with the average. It never returns emails. If the event is not published the answer is 404.
```json
{
  "reviews": [ { "id": "…", "participant_name": "Asha Rao", "rating": 5, "comment": "Loved it", "created_at": "…" } ],
  "average_rating": 4.5,
  "count": 8,
  "rating_distribution": { "5": 5, "4": 3, "3": 0, "2": 0, "1": 0 },
  "pagination": { "total": 8, "page": 1, "page_size": 8, "total_pages": 1 }
}
```
`participant_name` is the name on the attendee's registration, or null (show "Attendee").

#### DELETE /events/{event_id}/reviews/{review_id}

The author can delete their own review. Staff (admin or provider) of the business that owns the event can delete any review of it, and a Super Admin can delete any review. Response 200 `{"message": "Review deleted"}`. Someone else's review as a normal user gives 403 "You can only delete your own review", staff of another business 403 "Not authorized for this tenant", an unknown review or a feedback row 404. Staff deletions are written to the moderation history.

#### GET /events/{event_id}/reviews/me

Login required. The caller's own review in the same shape as the POST answer (any status), or `null`.

#### GET /events/{event_id}/reviews/manage

For the Enterprise Admin or the provider of the business that owns the event, or a Super Admin (a login is required). It returns every review of the event whatever its status, so the owner can find the `id` for the moderate call. Add `?status=pending`, `approved` or `rejected` to see one tab; any other status gives 400. Also `page` (default 1) and `page_size` (default 20, maximum 100).
```json
{
  "reviews": [
    {
      "id": "…", "event_id": "…", "participant_name": "Asha Rao", "participant_email": "asha@example.com",
      "rating": 5, "comment": "Loved it", "moderation_status": "pending", "created_at": "…"
    }
  ],
  "counts": { "pending": 3, "approved": 8, "rejected": 1 },
  "average_rating": 4.5,
  "pagination": { "total": 3, "page": 1, "page_size": 20, "total_pages": 1 }
}
```
`counts` is for the Pending, Approved and Rejected tabs and does not change with the `status` filter. This list shows the reviewer's email because the owner needs it to follow up; do not show it anywhere the public can see. A customer gets 403.

#### PATCH /events/{event_id}/reviews/{review_id}/moderate

Enterprise Admin of the business that owns the event, or an active Super Admin. A provider gets 403.
```json
{ "action": "approved" }
```
A review that is not on this event, or a feedback row that is not a review, gives 404.

#### POST /events/{event_id}/feedback and GET /events/{event_id}/feedback

POST needs a login and takes any of participant_email, form_id, answers, rating and comment. It is a form answer, not a review, and has no moderation. GET is only for event managers.

The event feedback report (`GET /events/{event_id}/reports?type=feedback`) puts its numbers in `data`: `total_feedbacks`, `total_reviews`, `approved_reviews` and `average_rating`. The average is taken from the approved reviews only and is null when there are none.


### Moderation queue (all modules)

#### GET /reviews/manage

Shows the reviews the caller can moderate, so an owner can work through them in one place. An Enterprise Admin sees the reviews of the items their own business owns. A provider sees the same but only for items they are allowed to see (a provider sees products and services assigned to them), and cannot moderate. A Super Admin sees every business. A customer gets 403.

Query (all optional):

| Name | Meaning |
|---|---|
| module | `training` (or `course`), `product`, `service`, `event`. Leave it out for all of them |
| status | `pending`, `approved` or `rejected` |
| item_id | only reviews of this training, product, service or event |
| rating | only reviews with this many stars (1 to 5) |
| q | text inside the comment (not case sensitive) |
| tenant_id | only the reviews of this business. A Super Admin can name any business (this is the business filter of the global approval page). Anyone else may only name their own business, otherwise 403 |
| page, page_size | default 1 and 20, maximum 100 |

Response 200
```json
{
  "items": [
    {
      "module": "product",
      "review_id": "…",
      "item_id": "…",
      "item_name": "Yoga Mat Pro",
      "rating": 4,
      "comment": "Solid product",
      "reviewer_name": "Ravi",
      "reviewer_email": null,
      "reviewer_user_id": "…",
      "is_verified": true,
      "tenant_id": "…",
      "business_name": "Acme Wellness",
      "moderation_status": "pending",
      "created_at": "…",
      "updated_at": "…"
    }
  ],
  "counts": { "pending": 2, "approved": 1, "rejected": 1 },
  "pagination": { "total": 4, "page": 1, "page_size": 20, "total_pages": 1 }
}
```
- Newest first, across all modules.
- `counts` is per status for the current filters (including `tenant_id`), ignoring `status`, for the Pending / Approved / Rejected tabs.
- `tenant_id` and `business_name` say which business owns the item. The global approval page shows `business_name` as a column.
- `reviewer_email` is filled for training and event reviews only (product and service reviews are tied to a user id). It is for moderators; never show it publicly.
- 422 for an unknown `module` or `status`, or a `rating` outside 1 to 5.

#### PATCH /reviews/{module}/{review_id}/moderate

Approve, reject or reset one review when you only have the module and the review id, for example from the queue above.
```json
{ "action": "approved" }
```
It returns the row in the same shape as one item of the queue. The same rules apply as for the module's own moderate API: Enterprise Admin of the owning business or Super Admin, providers get 403, another business gets 403, unknown review 404, unknown module 422, unknown action 400.


#### DELETE /reviews/{module}/{review_id}

Delete one review when you only have the module and the review id, for example from a row of the queue. `module` is `training` (or `course`), `product`, `service` or `event`.

Same rules as the module's own delete: the author can delete their own review; staff (admin or provider) of the business that owns the item can delete any review of it; a Super Admin can delete any review. Response 200 `{"message": "Review deleted"}`. Someone else's review as a normal user gives 403 "You can only delete your own review", staff of another business 403 "Not authorized for this tenant", an unknown review 404, an unknown module 422. Deleting someone else's review is written to the moderation history.

#### GET /reviews/audit

The moderation history, newest first. An Enterprise Admin sees the history of their own business; a Super Admin sees every business. A provider or a customer gets 403.

Query (all optional): `module` (`training`, `product`, `service`, `event`), `review_id`, `item_id`, `action` (the new status: `approved`, `rejected`, `pending` or `deleted`), `actor_user_id`, `tenant_id` (only the history of one business: a Super Admin can name any, anyone else only their own, otherwise 403), `page`, `page_size`. A wrong `module` or `action` gives 422.

Response 200
```json
{
  "items": [
    {
      "id": "…", "module": "product", "review_id": "…", "item_id": "…", "item_name": "Yoga Mat Pro",
      "from_status": "pending", "to_status": "approved",
      "actor_user_id": "…", "actor_role": "admin", "created_at": "…"
    }
  ],
  "pagination": { "total": 1, "page": 1, "page_size": 20, "total_pages": 1 }
}
```
- A row is written every time the status really changes, and every time staff delete someone else's review (`to_status: "deleted"`). Setting the status a review already has writes nothing. An author deleting their own review is not written.
- A problem writing the history never stops the moderator's action; it is logged on the server.


## 4. Notifications

They use the notification feed and the Socket.IO `notification` event that already exist (`GET /users/me/notifications`, the unread count, push). Nothing new is needed on the connection.

| Category | Sent to | When |
|---|---|---|
| review_submitted | the Enterprise Admin(s) of the business that owns the item | a new review is saved. Editing a review does not send it again |
| review_approved | the author | a moderator changes the status to approved |
| review_rejected | the author | a moderator changes the status to rejected |

Putting a review back to pending sends nothing, and neither does setting the status it already has.

Every one has `metadata.category` and these fields in `metadata`: `entity_type` (training, product, service or event), `entity_id` (the item), the matching `training_id`, `product_id`, `service_id` or `event_id`, `review_id` and `status`. `review_submitted` also has `rating`.

What the app does with them:
- `review_submitted` (owner): open the reviews queue. Use `review_id`, or open `GET /reviews/manage?status=pending`.
- `review_approved` and `review_rejected` (author): open the item (`entity_type` and `entity_id`) and show the review (`GET .../reviews/me`).

A review's author can only be told if the backend can identify them: always for product, service and event reviews written after this release, and for training reviews through the learner's enrolment. If it cannot, nothing is sent.


## 5. Guide for the frontend developers

### 5.1 Web and mobile

Which API to call for which screen

| Screen | Call |
|---|---|
| Training card | `average_rating` (null when none) and `reviews_count` are in the list response |
| Training detail, reviews | GET /trainings/{id}/reviews |
| Product or service card | `rating` (0 when none) and `reviews_count` are in the list response |
| Product or service detail, reviews | GET /products/{id}/reviews or GET /services/{id}/reviews |
| Event detail, reviews | GET /events/{id}/reviews (published events) |
| "My review" and the edit screen | GET .../reviews/me |
| Owner or Super Admin, reviews page | GET /reviews/manage, then PATCH /reviews/{module}/{review_id}/moderate |

When to show the "Write a review" button

| For | Show it when |
|---|---|
| Training | the `enrolment_status` in the training detail is enrolled, active, completed or approved |
| Product, service | the user is logged in |
| Event | the user is logged in and has a confirmed or attended registration |

Things to do in every app

- Send the rating as a number, not as text.
- Limit the comment box to 2000 characters.
- Never show an email address. For training and event reviews use `participant_name` and fall back to "Learner" or "Attendee". For product and service reviews use `reviewer_name` and fall back to "Customer".
- Convert the dates to local time. They are UTC, so add "Z" to the string before you parse it.
- Show "No ratings yet" when `average_rating` is null, and when a product or service card has `reviews_count` 0.
- After a review is sent, the answer has `moderation_status: "pending"` for a new review. Show "Thanks, your review will appear once it has been approved" and do not expect to see it in the public list. For an edited review the status is whatever it had.
- To show whether the user already reviewed, call `GET .../reviews/me` when the item page opens. If it is `null` show "Write a review", otherwise show their review with its status ("Awaiting approval", "Approved" or "Not approved") and an "Edit" button.

What to show for errors

| Status | Message |
|---|---|
| 401 | send the user to login and then back to the item |
| 403 when sending | "You need to be enrolled (or registered) to review this". You can also show the server text |
| 404 | "This item is not available any more" |
| 422 | "Choose 1 to 5 stars" or "The comment is too long" |

For the owner screens:
- 403 "Providers are read-only; Enterprise Admin access required": hide the Approve and Reject buttons for providers, they can still read the queue.
- 403 "Not authorized for this tenant": the review belongs to another business. This should not happen if the list comes from `/reviews/manage`.

Do not design for these yet, they do not exist: replies, helpful votes, photos, reporting a review.

### 5.2 Web

- If the web app uses the session cookie, send the requests with `credentials: "include"`. If it uses the access token, send the Bearer header.
- Ask for pages (`?page=1&page_size=20`) and add a "Show more" button, or a sort menu (`sort=highest`) and a stars filter (`rating=5`). The summary at the top (average, count, star bars) does not change with the filter.
- Comments can have line breaks. Show them as text, never as HTML.
- After a successful POST, do not reload the public list to look for the new review. It is pending and will not be there. Update the "my review" box instead.
- The owner page: use the tabs Pending, Approved and Rejected with `counts` for the numbers, call `/reviews/manage?status=pending&page=1`, and after Approve or Reject remove the row (or reload the page of the list).

Example: send a review
```js
const res = await fetch(`${API}/products/${productId}/reviews`, {
  method: "POST",
  headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
  body: JSON.stringify({ rating: 5, comment })
});
if (!res.ok) {
  const err = await res.json();
  // err.detail is a text, or a list when the status is 422
}
const review = await res.json();   // review.moderation_status is "pending" for a new one
```

Example: approve a review from the queue
```js
await fetch(`${API}/reviews/${row.module}/${row.review_id}/moderate`, {
  method: "PATCH",
  headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
  body: JSON.stringify({ action: "approved" })
});
```

Example: show a date
```js
const date = new Date(review.created_at + "Z");
date.toLocaleDateString();
```

### 5.3 Mobile

- Send the Bearer token on every call except the public GET calls. If you get 401, refresh the token the way the app normally does and try once more.
- Use `GET .../reviews/me` instead of saving the review on the phone. It always has the latest status.
- Read the dates as UTC. On Android use `LocalDateTime.parse(value).atOffset(ZoneOffset.UTC)`. On iOS use an ISO8601 formatter with fractional seconds and the time zone set to UTC.
- Ask for the lists in pages (`page` and `page_size=20`) and load the next page when the user scrolls to the end. Use `pagination.total_pages` to know when to stop.
- Cards: training cards use `average_rating` and `reviews_count`; product and service cards use `rating` and `reviews_count`; hide the stars when the count is 0.
- Disable the submit button while the request is running, and keep the text the user typed if the request fails.
- Push and in-app notifications: route on `metadata.category` as described in part 4.

### 5.4 What is still missing (backend)

1. Replies from the owner, helpful votes, photos in a review, reporting a review.
2. Settings (approval on or off per module, banned words, a time limit for editing) and reminders to review after a training or event.
3. Editing a review does not send it back to pending (open decision).
4. Enterprise-level ratings are not connected to these reviews.
5. Program reviews are not part of this and work as before.
