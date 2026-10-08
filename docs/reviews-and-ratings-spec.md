# Reviews and Ratings: Feature Document

This document explains how reviews and ratings work for the three kinds of users: the mobile customer, the Enterprise Owner and the Super Admin. It lists the features, the flow of each user, the fields and the APIs. The request and response samples are in the API document (`reviews-and-ratings-api.md`).

Reviews are available for four things: trainings (courses are the same thing as trainings and use the same data), products, services and events.

Everything here describes the backend as it is now. The last parts list what is still not built.


## 1. What changed in this release

Before this release, reviews worked differently in each module and some did not work end to end: owners could not find the pending reviews to approve, event reviews could not be read, training reviews went public without approval and showed reviewer emails, and the rating on product and service cards was always 0. All of that is fixed:

- All four modules follow the same rules (section 2).
- There is one moderation queue for the owner and the Super Admin that lists the pending reviews of every module (`GET /reviews/manage`), and one call to approve or reject any of them.
- Training and course reviews start as pending and are public only after approval. Reviews that already existed were marked approved, so nothing disappeared.
- Event reviews can be read (public list, and a list for the owner). The reviewer is the logged-in user, the rating is checked, and there is one review per person.
- Product and service approve and delete are limited to the business that owns the item.
- Rating on product, service and training cards comes from the real approved reviews.
- Every module has "my review", and the public lists have a star breakdown, sorting, a stars filter and paging.
- Authors can delete their own training and event reviews (as they could for products and services).
- A moderation history records who approved, rejected, reset or deleted which review (`GET /reviews/audit`).
- Notifications: the owner is told about a new review, the author about the decision.

Three database migrations are part of the release (section 9).


## 2. The rules (same for every module)

1. Every review starts as pending.
2. The public sees only approved reviews.
3. The Enterprise Owner of the business that owns the item, or a Super Admin, can approve or reject it. A provider (staff member) can see the reviews but cannot approve or reject.
4. A business can only moderate reviews of its own items.
5. A moderator can move a review from any status to any other: pending, approved or rejected.
6. If the author edits a review, the status stays as it was. So an approved review stays public after an edit. (This is open for decision, see section 11.)
7. The rating is a whole number from 1 to 5. There are no half stars.
8. The comment is optional, so a rating without a comment is fine. The comment can be at most 2000 characters.
9. A person has one review per item. Posting again changes the old review.
10. The average rating is calculated from approved reviews only. If there are no approved reviews the average is empty (null) and the app shows "No ratings yet". It never shows 0.0.
11. Nobody sees another person's email address in a public list. Show the name only.

Who is allowed to write a review:

- Training and course: the learner must be enrolled. Enrolment status enrolled, active, completed or approved is accepted. Learners who are pending, rejected, cancelled or on the waiting list cannot review.
- Product: any logged in user. If the user has a confirmed order for the product, the review is marked "verified purchase".
- Service: any logged in user. It is never marked verified, because we have no booking record to check.
- Event: the logged-in user must have a registration with status confirmed or attended. The email in the request body is ignored, so nobody can review as someone else.

Who the reviewer is shown as: the training review shows the name on the enrolment, the event review the name on the registration, the product and service review the account name. When there is no real name the field is empty and the app shows "Learner", "Attendee" or "Customer". An email address is never used as a name.


## 3. Mobile customer

This is the person using the app to buy, enrol or attend. They are called customer here, or learner for trainings.

### What the customer can do

- See the average rating, the number of reviews and the star breakdown of an item.
- Read the approved reviews of an item.
- Write a review: choose 1 to 5 stars and, if they want, type a comment.
- See their own review at any time, with its status (waiting for approval, approved, not approved), and change it.
- Delete their own review.
- Sort the reviews of an item (newest, oldest, highest, lowest), show only one star rating or only reviews with a comment, and load them page by page.
- See a "verified" mark on reviews from real buyers and enrolled learners.
- Get a notification when their review is approved or rejected.

### Flow: customer reviews a product

1. The customer opens a product. The app loads the approved reviews and shows the average rating, or "No ratings yet". It also asks for the customer's own review (`GET /products/{id}/reviews/me`). If there is none it shows "Write a review".
2. The customer taps "Write a review", picks the stars, types a comment if they want, and submits.
3. The server saves it as pending and returns the saved review.
4. The app shows "Thanks, your review will appear once it has been approved", and the product page now shows "Your review: awaiting approval" with an Edit button.
5. When the Enterprise Owner approves it, it appears in the list for everyone and the customer gets a notification "Review approved". If it is rejected they get "Review not approved".

If the customer is not logged in, the server answers 401. Send them to login and bring them back to the product. If they never bought the product they can still review it, but it will not have the verified mark.

### Flow: learner reviews a training

1. The learner opens a training they are enrolled in. The training detail already tells the app the learner's enrolment status, so the app shows "Write a review" only when the status allows it.
2. They choose the stars and comment and submit. The app does not send an email, the server takes the learner from the login.
3. The server saves the review as pending and returns it. The app shows the "awaiting approval" message.
4. After approval the review is public and the learner is notified.

If the learner is not enrolled, the server answers 403 with the text "Verified reviews only — must be enrolled to review". Show a friendly line like "Only enrolled learners can review this training".

### Flow: attendee reviews an event

1. After the event, the attendee opens it and submits stars and a comment. The app does not send an email either, the server uses the logged-in user and checks that they have a confirmed or attended registration.
2. The review is saved as pending and the app shows a thank you message.
3. Once approved, it appears in the event's review list (`GET /events/{id}/reviews`, public) and the attendee is notified.

### Screens the mobile team needs

1. Rating summary: the average with one decimal and the number of reviews in brackets, or "No ratings yet". Optionally the star breakdown as five bars.
2. Review list: the reviewer's name, stars, comment, date, verified mark. A message like "Be the first to review" when empty. Long comments cut after a few lines with "Read more".
3. Write a review: star selector, comment box with a 2000 character limit, submit button that is disabled while sending.
4. My review box with the status and the Edit button.
5. A short message for each error: 401 sign in, 403 you must be enrolled or registered, 404 item not found, 422 choose 1 to 5 stars or comment too long.

Dates come from the server in UTC without a "Z" at the end. Add the "Z" before converting to the phone's local time.


## 4. Enterprise Owner

This is the admin of a business. Their staff (providers) can open the same pages but cannot approve or reject.

### What the owner can do

- See the rating of their own trainings, events, products and services (cards, detail pages, the training and event dashboards).
- Open one queue with the reviews of all their items, any module, with tabs Pending, Approved and Rejected and the number in each tab (`GET /reviews/manage`).
- Filter the queue by module, item, stars and words in the comment.
- Approve or reject a review, or put it back to pending (`PATCH /reviews/{module}/{review_id}/moderate`).
- Delete a review of their own business (every module).
- See the moderation history of their own business.
- Get a notification when a new review arrives.

### Decision: where the owner approves reviews

The Enterprise Owner approves reviews inside each module, not on a separate page. On the list page of Events, Trainings, Products and Services there is a **Reviews** tab. It shows the reviews received for that module, with the tabs Pending, Approved and Rejected and Accept / Reject buttons. The number of pending reviews is shown on the Reviews tab as a badge. A provider sees the tab without the buttons.

The backend serves this without any change:

- The tab of a module asks for `GET /reviews/manage?module=event&status=pending` (use `training`, `product` or `service` on the other pages). The answer has `counts`, which gives the numbers for the badge and the three tabs.
- For one event, `GET /events/{id}/reviews/manage` gives the reviews of just that event.
- Accept and Reject call `PATCH /reviews/{module}/{review_id}/moderate`, or the module's own moderate call.

A single combined page for the owner is not needed. It stays possible, because `GET /reviews/manage` without `module` returns all modules.

### Flow: owner handles new reviews

1. A notification arrives: "New review: 2 star review on Yoga Mat Pro is waiting for approval".
2. The owner opens the list page of that module (for example Products) and taps the Reviews tab, then Pending. Each row shows the item name, stars, comment, reviewer name, verified mark and date.
3. The owner taps Approve or Reject. Approved reviews become public. Rejected reviews stay hidden.
4. The author gets a notification with the result.
5. If the owner changes their mind, they can open the Rejected tab and move a review back to pending or approved.

Event owners can also work from the event page: `GET /events/{id}/reviews/manage` lists the reviews of that one event with the same tabs.

### Screens for the owner

1. A Reviews tab on the list page of each module (Events, Trainings, Products, Services), with the sub-tabs Pending, Approved and Rejected (numbers from `counts`), a pending badge on the tab, and filters for item, stars and text.
2. A row with Accept and Reject buttons. Training and event rows also show the reviewer's email, because the owner may need to follow up. Never show it to the public.
3. The rating and number of reviews on each item's page in the owner dashboard.

Providers see the same tab but without the Accept and Reject buttons, because the server refuses them. A provider only sees the products and services assigned to them.


## 5. Super Admin

This is the platform staff who see all businesses.

### What the Super Admin can do

- Open the same queue and see the reviews of every business.
- Approve, reject or reset any review in any module, including events.
- Delete any review.
- See the moderation history of every business (`GET /reviews/audit`).
- See the training average across all businesses on the training report summary.

### What is not built for the Super Admin

- Handling of complaints (reporting a review) and settings such as switching approval on or off per module.

### Flow: Super Admin removes an abusive review

1. The Super Admin opens the queue, filters by text or by item, and finds the review.
2. Taps Reject (it disappears from the public) or Delete.
3. The author is told the review was not approved (not for a delete).
4. The action is in the moderation history.

### Screens for the Super Admin

The Super Admin has one global approval page, not a tab per module, because they work across businesses.

1. The pending reviews of all businesses together, with the sub-tabs Pending, Approved and Rejected (numbers from `counts`).
2. Filters: module, business (`tenant_id`), item, stars and text. Each row shows the business name.
3. Buttons: Approve, Reject and Delete on each row.
4. The moderation history (`GET /reviews/audit`): who approved, rejected or deleted which review, and when. It can be shown as a list on the same page, or per review.

Handling complaints (a customer or owner reporting a review) and the settings page (approval on or off per module) are planned for a later release. Until then every module always needs approval.


## 6. Fields

Training and course review (table `training_reviews`)

| Field | Notes |
|---|---|
| id | review id |
| training_id | the training |
| rating | 1 to 5, required |
| comment | optional, up to 2000 |
| participant_email | taken from the login. Returned only to the author and to staff, never in the public list |
| participant_name | name on the enrolment, can be empty |
| verified | always true |
| moderation_status | pending, approved or rejected. A new review is pending |
| created_at, updated_at | UTC |

Product review (`product_reviews`) and service review (`service_reviews`)

| Field | Notes |
|---|---|
| id | review id |
| product_id or service_id | the item |
| user_id | the author |
| reviewer_name | the account's name, empty when the account has none. Never an email |
| rating | 1 to 5, required |
| comment | optional, up to 2000 |
| is_verified_purchase | true only for products with a confirmed order. Always false for services |
| moderation_status | pending, approved or rejected. A new review is pending |
| created_at, updated_at | UTC |

Event review (`event_feedback`, rows with `is_review` true)

| Field | Notes |
|---|---|
| id | review id |
| event_id | the event |
| participant_email | the logged-in user's email, saved when the review is written |
| user_id | the author, saved when the review is written (empty on older reviews) |
| rating | saved as text, always a whole number 1 to 5 now |
| comment | optional, up to 2000 |
| is_review | true for a review, false for plain feedback |
| moderation_status | a new review is pending |
| created_at, updated_at | UTC |


## 7. API list

All paths start with `https://chat.wisdomtooth.tech/api/v1`. The samples are in the API document. For trainings, every `/trainings/...` path also works as `/courses/...`.

### For the customer (mobile and web)

| What | Call | Who |
|---|---|---|
| Read reviews, average, star breakdown | GET /trainings/{id}/reviews, GET /products/{id}/reviews, GET /services/{id}/reviews, GET /events/{id}/reviews | anyone |
| Write or edit a review | POST on the same paths | logged in (training: enrolled, event: registered) |
| My review, any status | GET .../reviews/me on the same paths | logged in |
| Delete own review | DELETE .../reviews/{review_id} on trainings, products, services and events | the author |
| Sort, filter, page | `sort`, `rating`, `with_comment`, `page`, `page_size` on the public GET lists | anyone |
| Send event feedback | POST /events/{id}/feedback | logged in |

Cards already carry the rating: training `average_rating` and `reviews_count`; product and service `rating` and `reviews_count`.

### For the Enterprise Owner

| What | Call | Notes |
|---|---|---|
| The queue of reviews (the Reviews tab of a module passes `module`) | GET /reviews/manage | filters: module, status, item_id, rating, q, page, page_size. Providers can read |
| Approve, reject or reset | PATCH /reviews/{module}/{review_id}/moderate | Enterprise Admin or Super Admin |
| The same, per module | PATCH /trainings/{id}/reviews/{review_id}/moderate, /products/..., /services/..., /events/... | same rules |
| Reviews of one event | GET /events/{id}/reviews/manage | status, page, page_size |
| Delete a review | DELETE .../reviews/{review_id} (every module), or DELETE /reviews/{module}/{review_id} from a row of the queue | staff of the owning business |
| The moderation history | GET /reviews/audit | Enterprise Admin (own business), Super Admin (all). Filters: module, review_id, item_id, action, actor_user_id |
| Training average | GET /trainings/reports/summary | admin, provider |
| Event dashboard average | GET /events/reports/summary | admin, provider |
| Event feedback report | GET /events/{id}/reports?type=feedback | numbers in `data` |

### For the Super Admin

The same calls, across all businesses, including the history. The global approval page uses `GET /reviews/manage` without `module`; its business filter is the `tenant_id` parameter (each row also carries `tenant_id` and `business_name`). Delete from a row of the queue is `DELETE /reviews/{module}/{review_id}`. The history can be filtered by business with `tenant_id` too.

### Notifications

They use the notification feed and the socket event we already have.

| Category | Sent to | When |
|---|---|---|
| review_submitted | the Enterprise Admin(s) of the owning business | a new review is saved (not on an edit) |
| review_approved | the author | a moderator approves it |
| review_rejected | the author | a moderator rejects it |

Putting a review back to pending, or setting the status it already has, sends nothing. Each notification has `review_id`, `entity_type`, `entity_id` and `status` so the app can open the right screen.


## 8. What is not built yet

- Replies from the owner, helpful votes, photos in a review, reporting a review.
- Settings, for example approval on or off per module, banned words, a time limit for editing.
- A rating for an Enterprise as a whole.
- Reminders to review after a training or event finishes.


## 9. For the backend team

- Three migrations: `e9a1c3b5d7f2` adds `moderation_status` and `updated_at` to `training_reviews` (all existing rows are set to approved), `f2b4d6a8c0e1` adds `user_id` and `updated_at` to `event_feedback`, and `a3c5e7f9b1d4` creates `review_moderation_log`. Run them before the new code starts.
- The rules shared by all modules are in `app/services/review_common.py`. Product and service share `app/services/catalog_reviews.py`. The queue is in `app/services/review_admin_service.py` and `app/api/v1/endpoints/review_admin.py`. Who may moderate is in `app/services/review_access.py`. Notifications are in `app/services/review_notifications.py` and the history in `app/services/review_audit.py`.
- Notification recipients come from the identity service, like the training and event notifications. If the owner is not notified, check `INVIGORATE_INTERNAL_API_KEY` and the diagnostics endpoint `GET /api/v1/admin/notifications/diagnostics`.
- Program reviews were not changed.


## 10. Problems that were fixed and the ones left

Fixed

1. No list of pending product, service and event reviews: the queue now lists them.
2. No way to read event reviews: public list and owner list added.
3. Event reviews trusted the email in the request: the reviewer is now the logged-in user, the rating is checked and there is one review per person.
4. The training review list showed reviewer emails: removed from public output.
5. Product and service approve and delete did not check the business: they do now, and approve checks the review belongs to the product in the URL.
6. Product, service and enterprise cards showed `rating` 0 from an old table: product, service and training cards now use the real approved reviews.
7. The event approve call refused the Super Admin: allowed now.
8. The event rating was not checked and the report average counted rejected reviews: fixed.
9. Product and service `reviewer_name` could be an email: never returned as a name now.
10. Training and course reviews had no approval, no comment limit and an average of 0 instead of null: fixed.
11. Event comments had no limit: 2000 characters.

Left

1. Editing a review keeps it approved, so content can change after approval (open decision).
2. Enterprise `rating` and `reviews_count` are still read from an old unconnected table.
3. A provider is allowed to delete a review of their business.


## 11. Questions for the product owner

Decided: owners approve inside a Reviews tab on each module's list page; the Super Admin uses one global approval page. Complaints and the settings page come later.

Still open:

1. Should an edited review need approval again?
2. How should the reviewer be shown: full name, first name with the first letter of the last name, or hidden?
3. Can a learner review right after enrolling, or only after some progress?
4. Should providers (staff) be allowed to delete reviews? They can now, inside their own business.
5. Should an event review need a check-in? A confirmed registration includes people who never came.
6. What happens to the reviews if a user is deleted or an item is archived?
7. Is a review with only stars acceptable everywhere?
8. Will services get a booking record? Until then the verified mark stays off for services.


## 12. Test checklist

- A review with a rating of 0, 6 or "five" is refused with 422. A missing event rating is refused too.
- A comment over 2000 characters is refused with 422 in every module.
- Posting twice from the same account leaves one review, with the second text and the same status.
- A learner who is pending, rejected, cancelled or on the waiting list cannot review a training. A user without a registration cannot review an event, even with someone else's email in the body.
- A new review is not in the public list. After approval it appears and the average changes. After rejection it disappears again.
- An item with no approved reviews shows "No ratings yet" (average null; product and service cards show 0 with a count of 0).
- No public list or screen shows an email address.
- A provider cannot approve or reject (403). An Enterprise Admin can. A Super Admin can too, in every module including events.
- An Enterprise Admin cannot approve, reject or delete another business's review (403), and a review cannot be approved through another item's URL (404).
- The queue shows only the caller's business, a Super Admin sees all, and `counts` stay the same when a status filter is used.
- A Super Admin can pick one business in the queue and in the history (`tenant_id`) and the counts follow it; an Enterprise Admin naming another business gets 403. Every row shows the business name.
- Delete from a queue row (`DELETE /reviews/{module}/{review_id}`) follows the same rules as the module's own delete and is written to the history.
- `GET .../reviews/me` is null before writing and shows the status after.
- Sorting (`highest`, `lowest`, `oldest`), the stars filter and paging change the list but not the average, the count or the star bars.
- An author can delete their own training, event, product or service review; a stranger gets 403; staff of another business get 403.
- Every approve, reject, reset and staff delete appears once in `GET /reviews/audit`, with who did it. Setting the same status again adds nothing. An Enterprise Admin sees only their own business; a provider gets 403.
- The owner gets one notification for a new review and none for an edit. The author gets one for approved or rejected, none for pending, and none when the status does not change.
- Dates show in the user's local time.
