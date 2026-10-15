# Reviews and Ratings: Feature Document

Application: Invigorate Health. Modules: Training (courses are the same thing), Event, Product, Service. Users: Customer, Enterprise Admin, Super Admin.

This document explains how reviews and ratings work for the three kinds of users, with the features, the flow of each user, the fields and the APIs. The request and response samples are in the API document (`reviews-and-ratings-api.md`). It describes the backend as it is now; the last parts list what is not built.


## 1. In short

- A customer gives 1 to 5 stars and, if they want, a comment. The review is **approved and public at once**. Nobody has to approve it first.
- The customer can see, edit and delete their own review.
- Only a **Super Admin** can change the status of a review afterwards: reject it, hold it back as pending, or approve it again. Every such action is written to a moderation history.
- An **Enterprise Admin** can see the reviews of their own business (Approved, Pending and Rejected tabs) and the moderation history, but cannot change anything.
- Only approved reviews are public. Pending and rejected reviews are hidden from everyone except their author, the Enterprise Admin of the business, and the Super Admin.


## 2. The rules (same for every module)

1. A new review is approved and public straight away.
2. The public, and every average and star count, uses approved reviews only.
3. Only a Super Admin can approve, reject or move a review to pending. An Enterprise Admin or a provider gets 403, even for the items of their own business.
4. What a Super Admin can do to a review in each status:

| Now | Can become |
|---|---|
| Approved | Rejected, Pending |
| Pending | Approved, Rejected |
| Rejected | Approved |

   Asking for the status a review already has does nothing. Moving a rejected review to pending is refused with 409, because pending is for reviews that are waiting, and a rejected review can only be approved again.
5. When the author edits a review:
   - Approved: the changes are saved and public at once. It stays approved. No new approval is needed.
   - Pending: the changes are saved and it stays pending until a Super Admin decides.
   - Rejected: the changes are saved and the review becomes **pending** (a resubmission). It stays hidden until a Super Admin approves it.
6. For **training and event** reviews a Super Admin must give a reason (3 to 500 characters) to reject a review or to delete someone else's review. The reason is kept in the moderation history, shown to the Enterprise Admin in the history, and sent in the notifications to the author and the owning Enterprise Admins (in the app, not in push). Approving, holding back, product and service reviews need no reason.
7. The author can delete their own review in any status; it is removed completely. A Super Admin can delete any review. Nobody else can, including the Enterprise Admin and the providers of the business.
   A review deleted by a Super Admin is not erased. It gets the status **deleted**: hidden from the public, the averages and the admin tabs, but still shown to its author with the label "Deleted", so they know what happened. The author can edit it to resubmit it (it goes to pending, like a rejected review) or remove it for good. A Super Admin cannot approve, reject or pend a deleted review.
8. The rating is a whole number from 1 to 5. The comment is optional, at most 2000 characters.
9. A person has one review per item. Posting again changes the old review.
10. The average is the mean of the approved reviews. With no approved review it is empty (null) and the app shows "No ratings yet", never 0.0.
11. Nobody sees another person's email address in a public list. Show the name only.

What the customer sees for each status:

| Status | Label | Public |
|---|---|---|
| approved | Approved | yes |
| pending | Waiting for Approval | no |
| rejected | Not Approved | no |
| deleted (by a Super Admin) | Deleted | no |

The server sends these as `status_label`, together with `is_public` and `edit_label` ("Save Changes", or "Resubmit Review" for a rejected review), so the apps do not have to translate the status themselves.

Who is allowed to write a review:

- Training and course: the learner must be enrolled (status enrolled, active, completed or approved). Learners who are pending, rejected, cancelled or on the waiting list cannot review.
- Product: any logged in user. If the user has a confirmed order for the product, the review is marked "verified purchase".
- Service: any logged in user. It is never marked verified, because we have no booking record to check.
- Event: the logged-in user must have a registration with status confirmed or attended. The email in the request body is ignored, so nobody can review as someone else.

Who the reviewer is shown as: the training review shows the name on the enrolment, the event review the name on the registration, the product and service review the account name. When there is no real name the field is empty and the app shows "Learner", "Attendee" or "Customer". An email address is never used as a name.


## 3. Customer (mobile and web)

The person who buys, enrols or attends. The reviews live in the existing Ratings & Reviews section of the training, event, product or service. There are no extra screens or tabs.

### What the customer can do

- See the average rating, the number of reviews and the star breakdown of an item, and read the approved reviews. Sort them (newest, oldest, highest, lowest), show one star rating or only reviews with a comment, and load them page by page.
- Write a review: 1 to 5 stars (required) and a comment (optional).
- See their own review at any time, with its date and its status label.
- Edit it: an approved review is saved and stays public, a pending review stays pending, a rejected review is resubmitted.
- Delete it, after confirming. If a Super Admin deleted it, they still see it marked "Deleted" and can resubmit or remove it.
- Get a notification when a Super Admin approves or rejects their review.

### Flow: write a review

1. The customer opens the item and the Ratings & Reviews section. The app loads the approved reviews and the customer's own review (`GET .../reviews/me`). If there is none it shows "Write a review".
2. The customer picks the stars, types a comment if they want, and taps Submit Review.
3. The server saves it as approved and returns it. The review is in the public list at once.
4. The section now shows the customer's review with its date and the label "Approved".

### Flow: the Super Admin rejects the review

1. The review disappears from the public list and from the average. The customer gets a notification "Review not approved".
2. In the section the customer sees their review with the label "Not Approved" and an Edit Review button.
3. The customer edits it and taps Resubmit Review. The status changes to pending, the label is "Waiting for Approval", and the review stays hidden.
4. The Super Admins and the Enterprise Admins are told that a review was resubmitted.
5. If the Super Admin approves it, it is public again and the customer gets "Review approved". If it is rejected again, the customer can edit and resubmit again.

### Flow: the Super Admin holds the review back

The Super Admin moves an approved review to pending for checking. It is hidden, and the customer sees "Waiting for Approval". The customer can still edit it; it stays pending until the Super Admin decides. (There is no notification to the customer for pending, only for approved and rejected.)

### Screens the apps need

1. Rating summary: the average with one decimal and the number of reviews, or "No ratings yet". Optionally the star breakdown as five bars.
2. Review list: the reviewer's name, stars, comment, date, verified mark. "Be the first to review" when empty.
3. Write / edit a review: star selector, a comment box with a 2000 character limit, a button that shows `edit_label` and is disabled while sending.
4. My review box: the review, its date, `status_label`, Edit Review and Delete Review (with a confirmation).
5. A short message for each error: 401 sign in, 403 you must be enrolled or registered, 404 item not found, 422 choose 1 to 5 stars or comment too long.

Dates come from the server in UTC without a "Z" at the end. Add the "Z" before converting to local time.


## 4. Enterprise Admin

The admin of a business. View only.

### What the Enterprise Admin can do

- See the average rating of their own trainings, events, products and services (cards, detail pages, dashboards).
- Open the reviews of their business in three ways: from a notification, from one training, event, product or service, or from the Reviews tab of a module.
- See the reviews under three tabs: Approved (public), Pending (hidden, waiting for a Super Admin) and Rejected (hidden). The tab numbers come from `counts`.
- See for each review the module, the item, the customer name, the stars, the comment, the date and the status. New reviews and edits to approved reviews show up at once.
- See the moderation history of their own business: which Super Admin did what, the previous and the new status, and when.
- Get a notification when a customer posts or resubmits a review, and when a Super Admin moderates or deletes one.

### What the Enterprise Admin cannot do

- Approve, reject, move to pending, edit or delete a review. The server answers 403 "Only a Super Admin can moderate reviews" (or "You can only delete your own review").
- See the reviews or the history of another business.
- Providers (staff) have the same view-only access, limited to the products and services assigned to them. They cannot read the moderation history.

### Flow: the Enterprise Admin follows a review

1. A notification arrives: "A 5 star review was posted on Yoga Mat Pro". It carries `review_id`, `entity_type` and `entity_id`.
2. The admin opens the item or the Reviews tab of the module. The call is `GET /reviews/manage?module=product&status=approved`, and `item_id` narrows it to one item.
3. If a Super Admin later rejects or holds back the review, it moves to the Rejected or Pending tab, the admin gets a notification, and the change is in the history (`GET /reviews/audit`).
4. If the customer deletes the review, it is gone from every list.


## 5. Super Admin

The platform staff, who see all businesses.

### What the Super Admin can do

- Open the reviews in the same three ways, for every enterprise. The global page is `GET /reviews/manage` without `module`; its business filter is `tenant_id`.
- See for each review the enterprise name, module, item, customer name, stars, comment, date and status, under the Approved, Pending and Rejected tabs.
- Reject an approved or pending review. It is hidden from the public and the customer is notified.
- Move an approved review to pending when it needs checking.
- Approve a pending or rejected review. It is public again and the customer is notified.
- Handle reviews that customers edited or resubmitted: an edited approved review is already public and can be moderated if needed, a resubmitted review waits in Pending, an edited pending review stays pending.
- Delete a review.
- See the moderation history across all enterprises.

### The moderation history

Every Super Admin action is recorded with the enterprise name, the module, the item, the customer name, the Super Admin's name, the action, the previous status, the new status and the date and time. The current status of a review is the result of the latest entry. A review can have many entries. Customer edits, resubmissions and deletions by the author are not Super Admin actions and are not in the history.

Example (Rahul Mehta's review of the summit): 11:45 Meera Krishnan moved it from Approved to Pending; 14:10 Meera Krishnan approved it, Pending to Approved. Current status: Approved.

### Screens for the Super Admin

1. The global reviews page with the tabs Approved, Pending and Rejected (numbers from `counts`), filters for module, enterprise, item, stars and text, and the enterprise name on each row.
2. On each row only the buttons the status allows: Approved gives Reject and Move to Pending, Pending gives Approve and Reject, Rejected gives Approve. Delete is always there.
3. The moderation history, as a list on the same page or per review.


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
| moderation_status | approved, pending or rejected. A new review is approved |
| status_label, is_public, edit_label | for the customer's screen, see part 2 |
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
| moderation_status | approved, pending or rejected. A new review is approved |
| status_label, is_public, edit_label | for the customer's screen |
| created_at, updated_at | UTC |

Event review (`event_feedback`, rows with `is_review` true)

| Field | Notes |
|---|---|
| id | review id |
| event_id | the event |
| participant_email | the logged-in user's email, saved when the review is written |
| user_id | the author, saved when the review is written (empty on older reviews) |
| rating | saved as text, always a whole number 1 to 5 |
| comment | optional, up to 2000 |
| is_review | true for a review, false for plain feedback |
| moderation_status | approved, pending or rejected. A new review is approved |
| status_label, is_public, edit_label | for the customer's screen |
| created_at, updated_at | UTC |

Moderation history (`review_moderation_log`, one row per Super Admin action)

| Field | Notes |
|---|---|
| module, review_id, item_id, item_name | what was moderated |
| business_name | the enterprise that owns the item, as named at the time |
| customer_name | who wrote the review, as named at the time. Empty when only an email is known |
| from_status, to_status | the previous and the new status. `deleted` when a Super Admin removed the review |
| actor_user_id, actor_name, actor_role | the Super Admin |
| created_at | UTC |


## 7. API list

All paths start with `https://chat.wisdomtooth.tech/api/v1`. The samples are in the API document. For trainings, every `/trainings/...` path also works as `/courses/...`.

### Customer

| What | Call | Who |
|---|---|---|
| Read reviews, average, star breakdown | GET /trainings/{id}/reviews, /products/{id}/reviews, /services/{id}/reviews, /events/{id}/reviews | anyone |
| Write, edit or resubmit a review | POST on the same paths | logged in (training: enrolled, event: registered) |
| My review, any status | GET .../reviews/me on the same paths | logged in |
| Delete own review | DELETE .../reviews/{review_id} | the author |
| Sort, filter, page | `sort`, `rating`, `with_comment`, `page`, `page_size` on the public lists | anyone |

Cards already carry the rating: training `average_rating` and `reviews_count`; product and service `rating` and `reviews_count`.

### Enterprise Admin (view only)

| What | Call | Notes |
|---|---|---|
| Reviews of the business, any module, with the tabs | GET /reviews/manage | filters: module, status, item_id, rating, q, page, page_size. `counts` for the tabs |
| Reviews of one event | GET /events/{id}/reviews/manage | status, page, page_size |
| The moderation history of the business | GET /reviews/audit | filters: module, review_id, item_id, action, actor_user_id |
| Training average, event dashboard average, event feedback report | GET /trainings/reports/summary, GET /events/reports/summary, GET /events/{id}/reports?type=feedback | |

### Super Admin

| What | Call | Notes |
|---|---|---|
| All reviews, all enterprises | GET /reviews/manage | `tenant_id` picks one enterprise |
| Approve, reject, move to pending | PATCH /reviews/{module}/{review_id}/moderate, or the module's own PATCH .../reviews/{review_id}/moderate | `{"action": "approved" \| "rejected" \| "pending"}` |
| Delete any review | DELETE /reviews/{module}/{review_id}, or the module's own DELETE | written to the history |
| The history, all enterprises | GET /reviews/audit | `tenant_id` picks one enterprise |

### Notifications

They use the notification feed and the socket event we already have.

| Category | Sent to | When |
|---|---|---|
| review_submitted | the Enterprise Admin(s) of the owning business, and a copy to the Super Admin(s) | a customer posts a review, or edits a rejected one and resubmits it (`resubmitted: true`) |
| review_approved | the author | a Super Admin approves the review |
| review_rejected | the author | a Super Admin rejects the review |
| review_moderated | the owning Enterprise Admin(s) | a Super Admin approves, rejects or moves a review to pending |
| review_deleted | the owning Enterprise Admin(s) | a Super Admin deletes a review |

Moving a review to pending sends nothing to the author, and neither does setting the status it already has. An ordinary edit sends nothing. Each notification has `review_id`, `entity_type`, `entity_id` and `status` so the app can open the right screen.


## 8. What is not built yet

- Replies from the owner, helpful votes, photos in a review, reporting a review.
- Settings, for example banned words or a time limit for editing.
- A rating for an Enterprise as a whole (the enterprise `rating` is still read from an old table).
- Reminders to review after a training or event finishes.
- Program reviews are not part of this and work as before.


## 9. For the backend team

- Migrations: `e9a1c3b5d7f2` adds `moderation_status` and `updated_at` to `training_reviews` (existing rows are set to approved), `f2b4d6a8c0e1` adds `user_id` and `updated_at` to `event_feedback`, `a3c5e7f9b1d4` creates `review_moderation_log`, `a9c4e1b7d3f2` adds the moderator's name to it and `d4f8a2c6e1b9` adds the customer and enterprise names. Run them before the new code starts.
- Reviews that are pending at the time of the deploy stay pending until a Super Admin decides. Only reviews written after the deploy start as approved. If you want the old pending reviews published too, approve them from `GET /reviews/manage?status=pending`.
- The rules shared by all modules are in `app/services/review_common.py`, including the allowed status moves and the labels. Product and service share `app/services/catalog_reviews.py`. The queue is in `app/services/review_admin_service.py` and `app/api/v1/endpoints/review_admin.py`. Who may moderate is in `app/services/review_access.py` and the `require_review_moderator` dependency. Notifications are in `app/services/review_notifications.py` and the history in `app/services/review_audit.py`.
- Notification recipients come from the identity service, like the training and event notifications. If an admin is not notified, check `INVIGORATE_INTERNAL_API_KEY` and the diagnostics endpoint `GET /api/v1/admin/notifications/diagnostics`.


## 10. Questions for the product owner

1. Deleting: the document lets customers delete their own review but does not say whether a Super Admin may delete someone else's. The backend allows it for the Super Admin only (Enterprise Admins cannot) and records it in the history. Is that right?
2. Should a Super Admin be able to move a rejected review to pending? The document lists approve as the only way out of Rejected, so the backend refuses it.
3. How should the reviewer be shown: full name, first name with the first letter of the last name, or hidden?
4. Can a learner review right after enrolling, or only after some progress? Should an event review need a check-in? A confirmed registration includes people who never came.
5. What happens to the reviews if a user is deleted or an item is archived?


## 11. Test checklist

- A review with a rating of 0, 6 or "five" is refused with 422. A comment over 2000 characters is refused with 422 in every module.
- A new review is in the public list at once and counts in the average.
- Posting twice from the same account leaves one review with the second text. Editing an approved review keeps it approved; editing a pending review keeps it pending; editing a rejected review makes it pending and hidden.
- A learner who is not enrolled, and a user without a registration, cannot review a training or an event.
- A Super Admin can reject, hold back and approve (in the allowed moves). A rejected review cannot go to pending (409). The same status again does nothing and is not in the history.
- An Enterprise Admin and a provider get 403 when they try to approve, reject, move to pending or delete someone else's review, for their own business and for another one.
- Rejecting or holding back a review removes it from the public list and the average; approving puts it back. An item with no approved reviews shows "No ratings yet".
- The author can delete their own review in any status; a stranger gets 403.
- The customer's own review shows `status_label` "Approved", "Waiting for Approval" or "Not Approved".
- No public list or screen shows an email address.
- The queue shows only the caller's business, a Super Admin sees all, and `counts` stay the same when a status filter is used. Naming another business as an Enterprise Admin gives 403.
- Every Super Admin action appears once in `GET /reviews/audit` with the enterprise, item, customer, Super Admin, previous and new status. An Enterprise Admin sees only their own business; a provider gets 403.
- The Enterprise Admin and the Super Admin get one notification for a new review and one for a resubmission, none for an ordinary edit. The author gets one for approved or rejected, none for pending, none when nothing changes. The owning Enterprise Admins get one for each Super Admin action.
- Dates show in the user's local time.
