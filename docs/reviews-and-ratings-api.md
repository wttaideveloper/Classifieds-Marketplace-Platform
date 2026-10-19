# Reviews & Ratings API

Base URL: https://chat.wisdomtooth.tech/api/v1
Swagger: https://chat.wisdomtooth.tech/docs

A new review is public the moment it is posted. Only a Super Admin changes a status afterwards; Enterprise Admins can look at the reviews and the history of their own business but cannot change anything. This document is for the web and mobile developers. Part 1 is the list of APIs, part 2 the rules that are common to all of them, part 3 the details of each API with samples, part 4 the notifications, and part 5 a guide for the frontend. How the feature works for the customer, the Enterprise Owner and the Super Admin is explained in `reviews-and-ratings-spec.md`.

Reviews exist for trainings, products, services and events. Courses are the same as trainings, so every `/trainings/...` path also works as `/courses/...`.

Deploy note for the backend team: this feature needs these migrations (`e9a1c3b5d7f2` for training reviews, `f2b4d6a8c0e1` for event reviews, `a3c5e7f9b1d4` for the moderation history, `a9c4e1b7d3f2` for the moderator's name, `d4f8a2c6e1b9` for the customer and enterprise names in the history and `e8b2d5f1a7c3` for the reason in the history and `f9c3e6a2b8d4` for the reason shown to the author). Run them before the new code starts. Existing training reviews are marked approved by the first migration, so they stay visible. Reviews that are already pending when you deploy stay pending until a Super Admin decides on them; only reviews written after the deploy start as approved.


## 1. API list

Training and course

| Method | Path | Who can call | What it does |
|---|---|---|---|
| GET | /trainings/{training_id}/reviews | anyone | approved reviews, average, star count |
| POST | /trainings/{training_id}/reviews | logged in, enrolled | add, edit or resubmit own review |
| GET | /trainings/{training_id}/reviews/me | logged in | my own review, any status, or null |
| DELETE | /trainings/{training_id}/reviews/{review_id} | author, or Super Admin | delete a review |
| PATCH | /trainings/{training_id}/reviews/{review_id}/moderate | Super Admin | approve, reject or move to pending |

Product

| Method | Path | Who can call | What it does |
|---|---|---|---|
| GET | /products/{product_id}/reviews | anyone | approved reviews, average, star count |
| POST | /products/{product_id}/reviews | logged in | add, edit or resubmit own review |
| GET | /products/{product_id}/reviews/me | logged in | my own review, any status, or null |
| DELETE | /products/{product_id}/reviews/{review_id} | author, or Super Admin | delete a review |
| PATCH | /products/{product_id}/reviews/{review_id}/moderate | Super Admin | approve, reject or move to pending |

Service (same as product)

| Method | Path | Who can call | What it does |
|---|---|---|---|
| GET | /services/{service_id}/reviews | anyone | approved reviews, average, star count |
| POST | /services/{service_id}/reviews | logged in | add, edit or resubmit own review |
| GET | /services/{service_id}/reviews/me | logged in | my own review, any status, or null |
| DELETE | /services/{service_id}/reviews/{review_id} | author, or Super Admin | delete a review |
| PATCH | /services/{service_id}/reviews/{review_id}/moderate | Super Admin | approve, reject or move to pending |

Event

| Method | Path | Who can call | What it does |
|---|---|---|---|
| GET | /events/{event_id}/reviews | anyone | approved reviews, average, star count. Published events only |
| POST | /events/{event_id}/reviews | logged in, registered | add, edit or resubmit own review |
| GET | /events/{event_id}/reviews/me | logged in | my own review, any status, or null |
| DELETE | /events/{event_id}/reviews/{review_id} | author, or Super Admin | delete a review |
| GET | /events/{event_id}/reviews/manage | Enterprise Admin, provider, Super Admin | every review of the event with status, counts per status, paging |
| PATCH | /events/{event_id}/reviews/{review_id}/moderate | Super Admin | approve, reject or move to pending |
| POST | /events/{event_id}/feedback | logged in | send feedback |
| GET | /events/{event_id}/feedback | event manager | read feedback |

Moderation queue for all modules

| Method | Path | Who can call | What it does |
|---|---|---|---|
| GET | /reviews/manage | Enterprise Admin, provider (read only), Super Admin | every review the caller may moderate, any module and status, with counts and paging |
| PATCH | /reviews/{module}/{review_id}/moderate | Super Admin | approve, reject or move to pending, knowing only the module and the id |
| DELETE | /reviews/{module}/{review_id} | author, or Super Admin | delete a review knowing only its module and id |
| GET | /reviews/audit | Enterprise Admin (own business), Super Admin | the moderation history: which Super Admin approved, rejected, held back or deleted which review, and when |

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
- A new review is saved with `moderation_status: "approved"` and is public at once. No admin has to approve it first. The public lists show approved reviews only.
- **Reasons (all four modules).** A Super Admin must give a `reason` to **reject** a review, to **move it to pending** and to **delete** someone else's review. It is 3 to 500 characters (spaces around it are cut, line breaks become spaces). Missing, blank, shorter than 3 or longer than 500 gives 422 `{"detail": "A reason is required to reject a review"}` (or "... to move a review to pending", "... to delete a review", "The reason must be at least 3 characters", "The reason must be at most 500 characters"); a `reason` that is not text gives the usual 422 body error list. Nothing is changed when it is refused. Approving needs no reason, and neither does an author deleting their own review. The reason is saved in the moderation history (`reason` in `GET /reviews/audit`), sent as `metadata.reason` in the notifications (part 4), and **shown to the review's author** as `reason` on their own review (`my_review`, `GET .../reviews/me`, the answer to their POST) while the review is `rejected`, `pending` or `deleted`. It is cleared when the review is approved again or when the author edits a rejected or deleted review (the resubmission). A pending review the author edits keeps its reason. Nobody but the author and the staff sees it; it is never in the public lists.
- Every review answer also has `status`, the same as `moderation_status` (`approved`, `pending`, `rejected` or `deleted`).
- Only a Super Admin changes a status. The `action` is `approved`, `rejected` or `pending`. The allowed moves are: approved to rejected or pending, pending to approved or rejected, rejected to approved. Asking for the status the review already has does nothing and returns 200. Any other move (a rejected review to pending) gives 409. An Enterprise Admin or a provider gets 403 "Only a Super Admin can moderate reviews", even for the items of their own business.
- Editing: an approved review stays approved and the new text is public at once. A pending review stays pending. A rejected review goes back to `pending` when its author edits it (this is the resubmission), and stays hidden until a Super Admin approves it.
- Every review answer has `status_label` for the customer ("Approved", "Waiting for Approval", "Not Approved" or "Deleted"), `is_public` and `edit_label` (the button on the edit form: "Save Changes", or "Resubmit Review" for a rejected or deleted review).
- A person can delete their own review in any status; it is removed completely. A Super Admin can delete any review. Nobody else can.
- When a **Super Admin** deletes someone else's review, the review is not erased: it gets `moderation_status: "deleted"`, disappears from every public list, count and average and from the admin tabs, and its author still gets it in `my_review` / `GET .../reviews/me` with `status_label: "Deleted"`, `is_public: false` and `edit_label: "Resubmit Review"`. The author can then edit it (it goes to `pending` for a Super Admin to decide, like a rejected review) or delete it for good (then `my_review` is `null`). A deleted review cannot be moderated (409), and deleting it again changes nothing. When the **author** deletes their own review it is removed completely.
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
  "moderation_status": "approved",
  "status_label": "Approved",
  "is_public": true,
  "edit_label": "Save Changes",
  "created_at": "2026-10-07T09:15:30.123456",
  "updated_at": "2026-10-07T09:15:30.123456"
}
```
`verified` is always true, because only enrolled learners can post. The review is approved and is in the public list straight away. If the learner edits a rejected review, the answer has `moderation_status: "pending"` and `status_label: "Waiting for Approval"`, and the review is hidden until a Super Admin approves it. The enrolment's name is `participant_name`; it is null if the enrolment has no real name (an email is never used as a name).

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
Approved reviews only. `participant_email` is always null here. Show `participant_name`, and "Learner" when it is null. `average_rating` is null when there are none. A logged-in caller also gets `my_review` in this answer: their own review in any status, including `deleted` when a Super Admin deleted it. It is not counted in `reviews`, `count` or the average, and it is `null` only when the caller has no review (or removed it themselves).

GET /trainings/{id} has the same approved reviews in a `reviews` list (latest 50) with id, training_id, participant_name, rating, comment and created_at, and the fields `average_rating` (null when none) and `reviews_count`.

#### GET /trainings/{training_id}/reviews/me

Login required. Returns the caller's own review in the same shape as the POST answer (with `moderation_status`), or `null` when they have not written one. Use it to fill the edit screen and to show the status label ("Approved", "Waiting for Approval", "Not Approved" or "Deleted") after the app was closed. A review a Super Admin deleted is returned here with `moderation_status: "deleted"`.

#### DELETE /trainings/{training_id}/reviews/{review_id}

The author can delete their own review, whatever its status, and a Super Admin can delete any review. Response 200 `{"message": "Review deleted"}`. When a Super Admin deletes it, the review stays for its author with the status `deleted` (see part 2). Anyone else, including the Enterprise Admin and the providers of the business that owns the training, gets 403 "You can only delete your own review". An unknown review, or one from another training, gives 404. When a Super Admin deletes someone else's review it is written to the moderation history and the owning Enterprise Admins are told. To delete someone else's review a Super Admin sends a body `{ "reason": "Abusive language" }` (required, 3 to 500 characters, else 422). If your client cannot send a body with DELETE, use `?reason=Abusive%20language`. The author does not need a reason. Also available under `/courses/...`.

#### PATCH /trainings/{training_id}/reviews/{review_id}/moderate

Super Admin only.
```json
{ "action": "rejected", "reason": "Contains personal details" }
```
`reason` is required when `action` is `rejected` or `pending` (see part 2); for `approved` leave it out. Returns the review as in the POST answer. An Enterprise Admin or a provider gets 403 "Only a Super Admin can moderate reviews". A move that is not allowed (a rejected review to pending) gives 409 "A rejected review can only be moved to approved". A review that is not on this training gives 404. An action other than approved, rejected or pending gives 400. Also available as `/courses/{training_id}/reviews/{review_id}/moderate`.


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
  "moderation_status": "approved",
  "status_label": "Approved",
  "is_public": true,
  "edit_label": "Save Changes",
  "created_at": "2026-10-07T09:15:30.123456",
  "updated_at": "2026-10-07T09:15:30.123456"
}
```
A new review is approved and public at once. Editing keeps the status it had, except that editing a rejected review sends it back to pending (a resubmission). `reviewer_name` is the user's display name. It is null when the account has no name; an email address is never returned as a name (older reviews that had one are returned with null too). Show "Customer" for null.

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

The author can delete their own review. A Super Admin can delete any review. Response 200 `{"message": "Review deleted"}`. When a Super Admin deletes it, the review stays for its author with the status `deleted` (see part 2). Anyone else, including the Enterprise Admin and the providers of the business that owns the product, gets 403 "You can only delete your own review". An unknown review gives 404.

#### PATCH /products/{product_id}/reviews/{review_id}/moderate

Super Admin only. An Enterprise Admin or a provider gets 403 "Only a Super Admin can moderate reviews".
```json
{ "action": "approved" }
```
`action` is approved, rejected or pending. It returns the updated review. Any other value gives 400, a move that is not allowed gives 409 (see part 2). The review must belong to the product in the URL, otherwise 404.

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
- One review per person. A new review is approved and public at once. Sending it again changes it and keeps its status, except that a rejected review goes back to pending.

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
  "moderation_status": "approved",
  "status_label": "Approved",
  "is_public": true,
  "edit_label": "Save Changes",
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

If the caller is logged in, the answer also has `my_review`: their own review in any status, found by their user id. It is `null` when they have not written one, and for an anonymous reader. Their own approved review is in `my_review` and is **not repeated in `reviews`**, but it still counts in `count`, `average_rating` and `rating_distribution`; `pagination` describes the `reviews` list.
```json
{
  "my_review": {
    "id": "be3494b0-e974-4167-8a55-7061427de109",
    "participant_name": "Naga Suresh Inti",
    "rating": 5,
    "comment": "Good",
    "created_at": "2026-10-15T12:48:42.068189",
    "updated_at": "2026-10-15T12:48:42.068189",
    "status": "approved",
    "moderation_status": "approved",
    "status_label": "Approved",
    "is_public": true,
    "edit_label": "Save Changes"
  },
  "reviews": [ { "id": "9fe5…", "participant_name": "Suresh Inti1", "rating": 5, "comment": "Good edit", "created_at": "…" } ],
  "average_rating": 5.0, "count": 2,
  "rating_distribution": { "5": 2, "4": 0, "3": 0, "2": 0, "1": 0 },
  "pagination": { "total": 1, "page": 1, "page_size": 8, "total_pages": 1 }
}
```
`status` (same as `moderation_status`) is `approved`, `pending`, `rejected` or `deleted`. When it is not `approved` the review is not public and is not in `count` or the average. The sort, stars and comment filters and the paging do not change `my_review`. The call needs no login, but send the token to get `my_review`.

#### DELETE /events/{event_id}/reviews/{review_id}

The author can delete their own review, and a Super Admin can delete any review. Response 200 `{"message": "Review deleted"}`. When a Super Admin deletes it, the review stays for its author with the status `deleted` (see part 2). Anyone else, including the Enterprise Admin and the providers of the business that owns the event, gets 403 "You can only delete your own review". An unknown review or a feedback row gives 404. A Super Admin's deletion of someone else's review is written to the moderation history. It needs a body `{ "reason": "…" }` (or `?reason=`), 3 to 500 characters, else 422. The author does not need a reason.

#### GET /events/ and the numbers on each event

Every item of the events list (`GET /events/?status=published&page=1&page_size=50`) has `approved_reviews` (the number of approved reviews, 0 when there are none) and `average_rating` (the mean of the approved ratings rounded to 2 decimals, `null` when there are none, so show "No ratings yet"). Pending, rejected and deleted reviews are not counted.

#### GET /events/{event_id}/reviews/me

Login required. The caller's own review in the same shape as the POST answer (any status), or `null`.

#### GET /events/{event_id}/reviews/manage

For the Enterprise Admin or the provider of the business that owns the event, or a Super Admin (a login is required). It returns every review of the event whatever its status. It is view only for the Enterprise Admin and the provider; the `id` is used by a Super Admin for the moderate call. Add `?status=pending`, `approved` or `rejected` to see one tab; any other status gives 400. Also `page` (default 1) and `page_size` (default 20, maximum 100).
```json
{
  "reviews": [
    {
      "id": "…", "event_id": "…", "participant_name": "Asha Rao", "participant_email": "asha@example.com",
      "rating": 5, "comment": "Loved it", "moderation_status": "pending", "status_label": "Waiting for Approval", "created_at": "…"
    }
  ],
  "counts": { "pending": 3, "approved": 8, "rejected": 1 },
  "average_rating": 4.5,
  "pagination": { "total": 3, "page": 1, "page_size": 20, "total_pages": 1 }
}
```
`counts` is for the Pending, Approved and Rejected tabs and does not change with the `status` filter. This list shows the reviewer's email because the owner needs it to follow up; do not show it anywhere the public can see. A customer gets 403.

#### PATCH /events/{event_id}/reviews/{review_id}/moderate

An active Super Admin only. An Enterprise Admin or a provider gets 403 "Only a Super Admin can moderate reviews".
```json
{ "action": "pending", "reason": "Needs a second look" }
```
`reason` is required for `rejected` and `pending` (3 to 500 characters, else 422) and left out for `approved`.
A review that is not on this event, or a feedback row that is not a review, gives 404.

#### POST /events/{event_id}/feedback and GET /events/{event_id}/feedback

POST needs a login and takes any of `form_id`, `answers` (the questionnaire answers), `rating` and `comment`. The attendee is the logged-in user: their token's email is saved, and a `participant_email` in the body is used only when the token has none. It is a form answer, not a review, and has no moderation. The event's Enterprise Admin(s) and the Super Admin(s) get an `event_feedback_submitted` notification (see `event-feedback.md`).

GET lists the forms newest first, each with `participant_name`, `participant_email`, `form_id`, `answers`, `rating`, `comment`, `is_review` and `created_at`. It is for the event's managers (the owning Enterprise Admin or provider) and an active Super Admin. It returns `[]` when there is no feedback. 401 not logged in, 403 for anyone else, 404 unknown event.

The event feedback report (`GET /events/{event_id}/reports?type=feedback`, also readable by an active Super Admin, who can read no other report type) puts its numbers in `data`: `total_feedbacks`, `total_reviews`, `approved_reviews` and `average_rating`. The average is taken from the approved reviews only and is null when there are none.


### Moderation queue (all modules)

#### GET /reviews/manage

Shows the reviews of every module in one place, with the Pending, Approved and Rejected tabs. An Enterprise Admin sees the reviews of the items their own business owns (view only). A provider sees the same but only for items they are allowed to see (a provider sees products and services assigned to them), and cannot moderate. A Super Admin sees every business and is the only one who can act on a row. A customer gets 403. To see the reviews of one training, product, service or event, add `item_id`.

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
      "status_label": "Waiting for Approval",
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

Approve, reject or move to pending one review when you only have the module and the review id, for example from the queue above. Super Admin only.
```json
{ "action": "rejected", "reason": "Contains personal details" }
```
`reason` is required when `action` is `rejected` or `pending`, for every module (3 to 500 characters, else 422 with `{"detail": "A reason is required to reject a review"}` or `"... to move a review to pending"`). Approving needs none.
It returns the row in the same shape as one item of the queue. The same rules apply as for the module's own moderate API: Super Admin only (an Enterprise Admin or a provider gets 403 "Only a Super Admin can moderate reviews"), a move that is not allowed gets 409, unknown review 404, unknown module 422, unknown action 400.


#### DELETE /reviews/{module}/{review_id}

Delete one review when you only have the module and the review id, for example from a row of the queue. `module` is `training` (or `course`), `product`, `service` or `event`.

Body (for a Super Admin deleting someone else's review, in any module):
```json
{ "reason": "Abusive language" }
```
The reason is required there (3 to 500 characters, else 422 `{"detail": "A reason is required to delete a review"}`). If your client cannot send a body with DELETE, send `?reason=Abusive%20language` instead. The author deleting their own review needs no reason.

Same rules as the module's own delete: the author can delete their own review (removed completely); a Super Admin can delete any review (kept for its author with the status `deleted`). Response 200 `{"message": "Review deleted"}`. Anyone else (including the Enterprise Admin of the owning business) gets 403 "You can only delete your own review", an unknown review 404, an unknown module 422. A Super Admin deleting someone else's review is written to the moderation history.

#### GET /reviews/audit

The moderation history, newest first. Every row is something a Super Admin did to a review. An Enterprise Admin sees the history of their own business (view only); a Super Admin sees every business. A provider or a customer gets 403.

Query (all optional): `module` (`training`, `product`, `service`, `event`), `review_id`, `item_id`, `action` (the new status: `approved`, `rejected`, `pending` or `deleted`), `actor_user_id`, `tenant_id` (only the history of one business: a Super Admin can name any, anyone else only their own, otherwise 403), `page`, `page_size`. A wrong `module` or `action` gives 422.

Response 200
```json
{
  "items": [
    {
      "id": "…", "module": "product", "review_id": "…", "item_id": "…", "item_name": "Yoga Mat Pro",
      "business_name": "Acme Wellness", "customer_name": "Ravi",
      "from_status": "approved", "to_status": "pending",
      "reason": null,
      "actor_user_id": "…", "actor_role": "super_admin", "actor_name": "Meera Krishnan", "created_at": "…"
    }
  ],
  "pagination": { "total": 1, "page": 1, "page_size": 20, "total_pages": 1 }
}
```
- A row is written every time a Super Admin really changes a status, and every time a Super Admin deletes someone else's review (`to_status: "deleted"`). Setting the status a review already has writes nothing. An author deleting their own review is not written, and neither is a customer editing or resubmitting a review. The current status of a review is the `to_status` of its latest row, or its `moderation_status`.
- `reason` is the Super Admin's reason when they rejected, held back (pending) or deleted a review, in any module, as saved. It is `null` for approving and for rows written before reasons existed. Enterprise Admins see it for their own business.
- `business_name` (the enterprise that owns the item) and `customer_name` (who wrote the review) are saved when the action happens, so they stay the same if the names change later. They are `null` on rows written before this release, and `customer_name` is `null` when only an e-mail address is known.
- `actor_name` is the moderator's display name, so the app needs no extra lookup. It is saved when the action happens (so it is who they were at that time). Rows written before this field existed get the person's current name when the history is read. It is `null` when no real name can be found, and it is never an e-mail address.
- A problem writing the history never stops the moderator's action; it is logged on the server.


## 4. Notifications

They use the notification feed and the Socket.IO `notification` event that already exist (`GET /users/me/notifications`, the unread count, push). Nothing new is needed on the connection.

| Category | Sent to | When |
|---|---|---|
| review_submitted | the Enterprise Admin(s) of the business that owns the item, and a copy to the Super Admin(s) | a new review is posted (it is already approved), or a customer edits a rejected review and resubmits it (it waits for a Super Admin). Any other edit does not send it again |
| review_approved | the author | a Super Admin changes the status to approved |
| review_rejected | the author | a Super Admin changes the status to rejected. `metadata.reason` |
| review_pending | the author | a Super Admin moves the review to pending (holds it back). `metadata.reason`. Not sent when the author's own edit sends a rejected review back to pending |

Setting the status a review already has sends nothing. An ordinary edit by the author sends nothing.

`review_submitted` is sent for all four modules, and the Platform Super Admin(s) get a second copy with the same content. Anyone who is already an owner of the business gets it once. A new review reads "A 5 star review was posted on …"; a resubmission has the title "Review resubmitted", says it is waiting for approval, and has `resubmitted: true` in the metadata. Every `review_submitted` has `learner_name` (the reviewer's display name; left out when only an e-mail address is known), `item_id`, `item_name` and `status`. It also has `comment`: what the customer wrote, exactly as saved (up to 2000 characters), so the admins can read the review from the notification. It is left out when the customer wrote no comment, and it is in the in-app feed and the realtime event only: like `reason`, it is never put in the push payload.

What a Super Admin does is told to the owning Enterprise Admin(s), for all four modules:

| Category | Sent to | When |
|---|---|---|
| review_moderated | the Enterprise Admin(s) who own the item | a Super Admin approves, rejects or moves a review to pending |
| review_deleted | the Enterprise Admin(s) who own the item | a Super Admin deletes someone else's review. `metadata.reason` |
| review_deleted | the author (`metadata.recipient: "author"`) | a Super Admin deletes their review (any module). They still see it marked deleted. `metadata.reason` |

Metadata: `entity_type` (`training`, `product`, `service` or `event`), `entity_id` and `item_id` (the same), the matching `training_id`, `product_id`, `service_id` or `event_id`, `item_name`, `review_id`, `previous_status`, `new_status` (`approved`, `rejected`, `pending` or `deleted`), `action` (same as `new_status`), `actor_name`, `actor_id` and `actor_role` (`super_admin`). The Super Admin who acted is never told about their own action. Nothing is sent when the status does not change or when an author deletes their own review. `actor_name` is left out when the server cannot find the person's name (an e-mail address is never used).

**Reasons in notifications (all four modules).** `metadata.reason` is the Super Admin's reason on `review_rejected` (author), on `review_moderated` when `action` is `rejected` **or `pending`** (Enterprise Admins) and on `review_deleted` (Enterprise Admins and the author). It is in the in-app feed and the realtime `notification` event. It is **not** in the push payload (the push `data` block leaves out `reason`, as it always has), and the title and text of the notification never contain it, so nothing private shows on a lock screen: the mobile app reads the reason from the notification metadata in the app, or from the review itself (`my_review.reason`). Approve notifications carry no reason. The author is told when a Super Admin moves their review to pending (`review_pending`, below) as well as when it is rejected or deleted. The author's `review_deleted` notice reads "Your review of … was deleted by an administrator."

Example `review_pending` (to the author), title "Review waiting for approval", text "Your review of \"Yoga Basics\" is waiting for approval and is hidden for now.", sent in the app and as a push (the push has the title and text and the ids, not the reason):
```json
{ "category": "review_pending", "training_id": "8341…", "entity_type": "training", "entity_id": "8341…", "review_id": "6f8e…", "status": "pending", "reason": "Needs a second look" }
```
Example `review_submitted` (to an Enterprise Admin and, as a copy, to a Super Admin), title "New review", text "A 5 star review was posted on \"Yoga Basics\".":
```json
{ "category": "review_submitted", "training_id": "8341…", "entity_type": "training", "entity_id": "8341…", "item_id": "8341…", "item_name": "Yoga Basics", "review_id": "6f8e…", "rating": 5, "status": "approved", "learner_name": "Asha Rao", "resubmitted": false, "comment": "Clear and practical." }
```

Example `review_rejected` (to the author), title "Review not approved":
```json
{ "category": "review_rejected", "training_id": "8341…", "entity_type": "training", "entity_id": "8341…", "review_id": "6f8e…", "status": "rejected", "reason": "Contains personal details" }
```
Example `review_moderated` (to an Enterprise Admin), text "Super Admin Meera Krishnan rejected a review of \"Yoga Basics\".":
```json
{ "category": "review_moderated", "entity_type": "training", "entity_id": "8341…", "item_id": "8341…", "training_id": "8341…", "item_name": "Yoga Basics", "review_id": "6f8e…", "previous_status": "pending", "new_status": "rejected", "action": "rejected", "actor_name": "Meera Krishnan", "actor_id": "eae8…", "actor_role": "super_admin", "tenant_id": "ab4d…", "reason": "Contains personal details" }
```
Example `review_deleted` (to the author), title "Review deleted":
```json
{ "category": "review_deleted", "training_id": "8341…", "entity_type": "training", "entity_id": "8341…", "item_id": "8341…", "item_name": "Yoga Basics", "review_id": "6f8e…", "status": "deleted", "new_status": "deleted", "action": "deleted", "recipient": "author", "reason": "Abusive language" }
```
The Enterprise Admin's `review_deleted` has the same fields as `review_moderated` above, with `new_status` and `action` set to `deleted`.

Every one has `metadata.category` and these fields in `metadata`: `entity_type` (training, product, service or event), `entity_id` (the item), the matching `training_id`, `product_id`, `service_id` or `event_id`, `review_id` and `status`. `review_submitted` also has `rating`.

What the app does with them:
- `review_submitted` (Enterprise Admin or Super Admin): open the review. Use `review_id` and `entity_type`, or open `GET /reviews/manage?item_id=…`. A Super Admin can open `GET /reviews/manage?status=pending` for the resubmitted ones.
- `review_moderated` and `review_deleted` (Enterprise Admin): open the item and its history (`GET /reviews/audit?review_id=…`).
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
| Super Admin, reviews page | GET /reviews/manage, then PATCH /reviews/{module}/{review_id}/moderate |
| Enterprise Admin, reviews page (view only) | GET /reviews/manage and GET /reviews/audit |

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
- After a new review is sent, the answer has `moderation_status: "approved"`. It is already in the public list, so show "Thanks, your review is live". If the answer has `moderation_status: "pending"` (the user resubmitted a rejected review), show "Thanks, your review will be public once it has been approved".
- To show whether the user already reviewed, call `GET .../reviews/me` when the item page opens. If it is `null` show "Write a review", otherwise show their review with `status_label` ("Approved", "Waiting for Approval", "Not Approved" or "Deleted") and an "Edit" button.
- The button on the edit form is the `edit_label` of the review: "Save Changes", or "Resubmit Review" when the review is rejected. The user can edit a review in every status.
- A "Delete" button asks for a confirmation and calls DELETE; the review leaves the lists at once.
- A review with `moderation_status: "deleted"` is shown only to its author, for example "This review was deleted by an administrator", with the "Resubmit Review" button (`edit_label`) and a "Remove" button (DELETE) that clears it. It is never in the public list, so do not look for it there.

What to show for errors

| Status | Message |
|---|---|
| 401 | send the user to login and then back to the item |
| 403 when sending | "You need to be enrolled (or registered) to review this". You can also show the server text |
| 404 | "This item is not available any more" |
| 422 | "Choose 1 to 5 stars" or "The comment is too long" |

For the owner screens:
- 403 "Only a Super Admin can moderate reviews": show the reviews and the history to Enterprise Admins and providers, but hide the Approve, Reject, Move to Pending and Delete buttons. Only a Super Admin sees them.
- 422 "A reason is required to reject a review" / "... to move a review to pending" / "... to delete a review": ask the Super Admin for a reason (3 to 500 characters) in the Reject, Move to Pending and Delete dialogs before you send the request, and show the server text if it still fails.
- On the customer's screen (web and mobile) show `reason` under the status of the customer's own review when it is not null, for example "Not approved: Contains personal details", "Waiting for approval: Needs a second look" or "Deleted: Abusive language". It is `null` for an approved review.
- 409 "A rejected review can only be moved to approved": hide "Move to Pending" on a rejected review. Offer only the moves the status allows: approved gives Reject and Move to Pending, pending gives Approve and Reject, rejected gives Approve.

Do not design for these yet, they do not exist: replies, helpful votes, photos, reporting a review.

### 5.2 Web

- If the web app uses the session cookie, send the requests with `credentials: "include"`. If it uses the access token, send the Bearer header.
- Ask for pages (`?page=1&page_size=20`) and add a "Show more" button, or a sort menu (`sort=highest`) and a stars filter (`rating=5`). The summary at the top (average, count, star bars) does not change with the filter.
- Comments can have line breaks. Show them as text, never as HTML.
- After a successful POST, update the "my review" box from the answer. The new review is in the public list at once, so you can also reload the list. A resubmitted review is pending and will not be there.
- The review pages: use the tabs Pending, Approved and Rejected with `counts` for the numbers, call `/reviews/manage?status=approved&page=1`. After a Super Admin moves a review, it appears under the tab of its new status.

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
const review = await res.json();   // review.moderation_status is "approved" for a new one
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
2. Settings (banned words, a time limit for editing) and reminders to review after a training or event.
3. Enterprise-level ratings are not connected to these reviews.
4. Program reviews are not part of this and work as before.
