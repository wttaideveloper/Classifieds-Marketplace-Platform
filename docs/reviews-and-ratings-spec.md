# Reviews and Ratings: Feature Spec

Written for the mobile and web developers, the people building the Enterprise Owner and Super Admin screens, and QA. The request and response JSON for each endpoint is in `reviews-and-ratings-api.md`. This document covers what the feature does for each kind of user, how it is supposed to flow, and where the backend does not support it yet.

Everything below was checked against the code on the `shree` branch. Nothing was tested against production.

## 1. Where things stand

Reviews exist for five things: trainings, products, services, programs and events. They were built one module at a time, so they behave differently, and some do not work end to end. Three problems matter most.

1. Product and service reviews never become visible. A new review is saved as pending and only approved reviews are shown publicly. But there is no endpoint that lists pending reviews, and the approve call needs a review id. Only the author ever sees that id, so no owner or admin can find the review to approve. The public list stays empty.
2. Event reviews can be written but not read. There is no list of them anywhere, public or for admins. The only place they show up is a count and an average inside the event feedback report.
3. Training reviews work, and they go live immediately, but the public list includes each reviewer's email address. The apps must not display it, and the backend should stop sending it.

Until the first two are fixed, the app can post reviews but cannot show them for products, services or events.

## 2. Who uses it

Customer or learner. Anyone using the mobile app or the website. They read reviews to decide, and write one after they have bought, enrolled or attended.

Enterprise Owner. The Enterprise Admin of a business on the platform. They want to know what customers say about their own trainings, events, products and services, and they want to reject or remove reviews that are unfair or abusive. Staff under the owner (providers) can see the same data but cannot approve or reject product and service reviews.

Super Admin. Platform staff who work across all businesses. They step in when an owner and a customer disagree, and they remove anything abusive.

## 3. Rules

### Who can review

- Training: the person must be enrolled (status enrolled, active, completed or approved). Pending, rejected, cancelled and waitlisted learners get a 403. Every training review counts as verified.
- Product: any signed-in user. It is marked as a verified purchase only if that user has a confirmed order containing the product.
- Service: any signed-in user. It is never marked verified, because there is no booking record to check.
- Program: the email in the request must belong to an enrolment on that program.
- Event: the email in the request must belong to a registration that is confirmed or attended.

### One review per person

For trainings, products and services, posting again edits the person's existing review. Both the first post and an edit return 201. Programs and events do not do this; every post creates a new review.

### Rating and comment

The rating is a whole number from 1 to 5. The comment is optional, so a stars-only review is valid. Products and services limit the comment to 2000 characters. The other modules set no limit, so the apps should apply 2000 themselves. There are no titles, photos or half stars.

### Visibility

- Training: public as soon as it is posted. No moderation.
- Product and service: starts as pending. The public sees approved reviews only. An Enterprise Admin or Super Admin approves or rejects.
- Program: public as soon as it is posted, but only signed-in users can read the list.
- Event: starts as pending. Nothing displays it yet.

A moderator can move a review between pending, approved and rejected in any direction. Editing a review does not send it back to pending, so an approved review can be rewritten afterwards without anyone seeing the change.

### Averages

- Training and program: the mean of all reviews, two decimals, and 0 when there are none.
- Product and service: the mean of approved reviews only, and null when there are none. The apps should show "No ratings yet" for null, not 0.0.
- Event: the feedback report averages every review record, including pending and rejected ones, which skews it.

### Privacy

Do not show any email address. The training list returns `participant_email`, so use `participant_name`, and fall back to something like "Learner" when it is empty or looks like an email. Product and service reviews use `reviewer_name`, which falls back to the email when the account has no display name, so hide any value containing an @. Timestamps are UTC with no timezone marker, so convert them to local time for display.

## 4. What each user can do

### Customer or learner

Works today:
- Read reviews and the average for trainings (public), products and services (approved only), programs (signed-in).
- Write a review in all five modules, subject to the rules above.
- Edit their own review for training, product and service by posting again.
- Delete their own product or service review.
- See the verified badge.

Does not exist:
- A way to fetch "my review" for an item. After the app is closed, a customer cannot see whether their product review is still pending.
- Pagination. Every list returns everything.
- Sorting or filtering (newest, highest, lowest, only with comments).
- A breakdown of how many 5, 4, 3, 2 and 1 star reviews there are.
- Helpful votes, reporting an abusive review, photos.
- A reminder to review after a training or event finishes.

### Enterprise Owner

Works today:
- Training dashboard average (`GET /trainings/reports/summary`) and the average and review count on each training.
- Event dashboard average, and the event feedback report with a review count and average.
- Approve, reject or reset a product or service review, and the same for an event review, if they already have the review id.
- Delete a product or service review.

Does not exist:
- A list of reviews to moderate. This is the blocker described in section 1.
- Any filter by item, rating, status or date.
- A notification when a review arrives. The platform notification feed exists but nothing sends review notifications.
- Replying to a review, flagging one to the Super Admin, exporting reviews.
- A product or service rating dashboard.

### Super Admin

Works today:
- The same approve, reject and delete calls, across all businesses, for products and services.
- The training summary covers all businesses for them.

Does not exist:
- A moderation queue across businesses.
- An audit trail. Moderation overwrites the status and keeps no record of who did it or when.
- Event moderation. The event moderate endpoint requires the admin role, so a Super Admin gets refused.
- Settings such as turning moderation on or off per module, a banned-word list or an edit window.
- Handling of reports, since reports do not exist yet.

## 5. Flows

### A customer reviews a product they bought

1. The customer opens the product. The app calls `GET /products/{id}/reviews` and shows the approved reviews and the average, or "No ratings yet" when the average is null.
2. They tap "Write a review", choose 1 to 5 stars, optionally type a comment, and submit with `POST /products/{id}/reviews`.
3. The response is 201 with `moderation_status: "pending"`. If they have a confirmed order for the product, `is_verified_purchase` is true.
4. The app shows "Thanks, your review will appear once it has been approved" and keeps the returned review on the device so the customer can see and edit it.

If the customer is not signed in, the call returns 401, so send them to sign in and bring them back. If they never bought the product they can still review it, just without the verified badge. A comment over 2000 characters returns 422.

### A customer edits their review

There is no "my review" endpoint, so the app has to rely on the copy it stored after posting, or look for the customer's `user_id` in the public list, which only works once the review is approved. Editing is the same POST as creating. For products and services the review keeps its current status, so an approved review stays public.

### A learner reviews a training

1. The learner opens a training they are enrolled in. `GET /trainings/{id}/reviews` returns the list and the average.
2. They submit stars and an optional comment with `POST /trainings/{id}/reviews`. The reviewer is taken from the sign-in, so the app should not send an email.
3. The response is 201 and the review is public straight away, so the app reloads the list.
4. If the learner is pending, rejected, cancelled or waitlisted, the call returns 403 "Verified reviews only, must be enrolled to review". The app should hide the button for them. The training detail already returns the caller's `enrolment_status`, so the app can decide before showing it.

### An attendee reviews an event

The app posts `POST /events/{id}/reviews` with the attendee's email, a rating and a comment. The server checks that the email belongs to a confirmed or attended registration, but it does not check that the email belongs to the signed-in user. The review is stored as pending. Nothing displays it afterwards.

### A participant reviews a program

`POST /programs/{id}/reviews` with email, rating and comment. It is public at once. There is no edit and no duplicate check, so the app should disable the button after a successful post.

### An Enterprise Owner moderates product and service reviews

This is how it should work once the review list exists.

1. A notification tells the owner that a new review arrived.
2. They open Reviews, then Pending, and see reviews for their products and services with item name, stars, comment, reviewer, verified badge and date.
3. They approve it, reject it, or leave it for later. A rejected review can be moved back to pending.
4. The author is notified of the outcome.

Today the owner cannot get past step 1 because the review list and the notifications do not exist.

### A Super Admin handles a complaint

Reporting does not exist yet, so this is the intended flow, not a current one. A customer or owner reports a review. It lands in a queue the Super Admin can see across all businesses. The Super Admin reads it with its context, then rejects or deletes it and adds a note. The action is logged, and the reporter and author are told.

### An owner checks how a training is rated

The owner opens the training dashboard. `GET /trainings/reports/summary` gives the overall average and each training card has `average_rating` and `reviews_count`. Opening a training shows its reviews. There is no way to filter, reply to or reject a training review.

## 6. Screen notes for the apps

- Rating summary: handle 0, null and a real average differently. One decimal place is enough. Show the count in brackets.
- Review list: needs an empty state ("Be the first to review"), collapsing of long comments, a verified badge, relative dates, no emails, and a cap on how many are rendered since there is no pagination.
- Write-a-review form: required star picker, optional comment up to 2000 characters, submit disabled while posting, and two different success messages (live now, or awaiting approval).
- Check eligibility before showing the button. Do not wait for the 403.
- Error mapping: 401 means sign in, 403 means "you need to be enrolled or registered to review", 404 means the item is gone, 422 means a bad rating or too long a comment.

## 7. Fields

Training review (`training_reviews`): `id`, `training_id`, `rating` (1 to 5, required), `comment`, `participant_email` (from the sign-in, do not display), `participant_name` (from the enrolment, can be empty), `verified` (always true), `created_at`.

Product and service review (`product_reviews`, `service_reviews`): `id`, `product_id` or `service_id`, `user_id`, `reviewer_name` (display name, or the email if there is none), `rating` (1 to 5, required), `comment` (up to 2000), `is_verified_purchase`, `moderation_status` (pending, approved or rejected, starts as pending), `created_at`, `updated_at`.

Program review (`program_reviews`): `id`, `program_id`, `participant_email` (required in the request body), `rating` (1 to 5, required), `comment`, `created_at`. The response says `verified: true` but it is not stored. No moderation.

Event feedback and review (`event_feedback`): `id`, `event_id`, `participant_email`, `form_id`, `answers` (free-form JSON), `rating` (stored as text and not validated), `comment`, `is_review` (true for a review, false for plain feedback), `moderation_status`, `created_at`.

## 8. APIs

Base URL: `https://chat.wisdomtooth.tech/api/v1`.

### Customer or learner

| Purpose | Call | Who |
|---|---|---|
| List training reviews and average | `GET /trainings/{id}/reviews` | anyone |
| Post or edit a training review | `POST /trainings/{id}/reviews` | signed in and enrolled |
| List product or service reviews (approved) | `GET /products/{id}/reviews`, `GET /services/{id}/reviews` | anyone |
| Post or edit a product or service review | `POST /products/{id}/reviews`, `POST /services/{id}/reviews` | signed in |
| Delete own product or service review | `DELETE /products/{id}/reviews/{review_id}`, same for services | the author |
| List program reviews | `GET /programs/{id}/reviews` | signed in |
| Post a program review | `POST /programs/{id}/reviews` | signed in and enrolled |
| Post an event review | `POST /events/{id}/reviews` | signed in and registered |
| Post event feedback | `POST /events/{id}/feedback` | signed in |

Training cards already carry `average_rating` and `reviews_count` on the list, detail and wishlist endpoints.

### Enterprise Owner

| Purpose | Call | Notes |
|---|---|---|
| Training average across their trainings | `GET /trainings/reports/summary` | admin or provider |
| Event feedback, review count, average | `GET /events/{id}/reports?type=feedback` | event manager; average includes rejected reviews |
| Event dashboard average | `GET /events/reports/summary` | admin or provider |
| Approve, reject or reset a product or service review | `PATCH /products/{id}/reviews/{review_id}/moderate`, same for services, body `{"action": "approved"}` (or rejected, pending) | admin only, providers get 403; needs a review id |
| Moderate an event review | `PATCH /events/{id}/reviews/{review_id}/moderate` | admin of the owning business |
| Delete a product or service review | `DELETE .../reviews/{review_id}` | admin, provider or super admin |
| List reviews to moderate | proposed: `GET /reviews/manage?module=&status=&item_id=&page=&page_size=` | does not exist yet |
| Reply to a review | proposed: `PUT /reviews/{module}/{review_id}/reply` | does not exist yet |

### Super Admin

The same moderate and delete calls above work across businesses for products and services. Event moderation refuses a Super Admin today. Training summary covers all businesses. A cross-business queue and an audit trail (proposed `GET /reviews/audit?review_id=`) do not exist yet.

### Notifications to add

These should use the existing notification feed and the generic socket event, not a new system.

- `review_submitted`: to the owner of the item, when a review is saved.
- `review_approved`: to the author, when a moderator approves.
- `review_rejected`: to the author, when a moderator rejects.

## 9. Suggested order of work

First, so the feature works at all:
1. A moderator list with status filter and pagination, limited to the caller's business. About a few days. Without it nothing in products, services or events can be reviewed by an admin.
2. Stop returning reviewer emails in the training list. About a day.
3. Event reviews: take the email from the sign-in instead of the request, validate the rating, allow one per attendee, add a public list of approved reviews and a summary on the event page. A few days.
4. Make the `rating`, `reviews` and `reviews_count` fields on product, service and enterprise responses read the real review tables. Right now `rating` is fixed at 0 and the other two come from an old unlinked store. A few days.
5. Check that the item belongs to the caller's business before moderating or deleting a product or service review. About a day.

Then, to make it pleasant to use:
6. Pagination, sorting and filters on the lists.
7. A "my review" endpoint.
8. The three notifications.
9. An audit trail, an option to send edited reviews back to pending, and the cross-business queue for the Super Admin.
10. The star distribution counts.

Later, if wanted: reporting abuse, owner replies, helpful votes, photos, review reminders after completion, and moderation settings.

I could not recover the original 31-item feature list from this session's history. If you paste it again I can match each item to the numbers above and mark which ones are already covered.

## 10. Problems in the current code

1. Products and services have no way to list pending reviews, so moderation is unusable.
2. Events have no way to read reviews at all.
3. Event and program reviews trust the email in the request body, so any signed-in user can post as another registered person.
4. The training list returns reviewer emails publicly.
5. Product and service moderate and delete do not check that the review belongs to the caller's business, and the moderate call ignores the product id in the URL. An owner could act on another business's review.
6. Editing a product or service review keeps it approved.
7. Product, service and enterprise responses show a fixed `rating` of 0 and take `reviews` and `reviews_count` from an unlinked store, so cards show wrong numbers.
8. Event moderation requires the admin role, so a Super Admin is refused.
9. Event `rating` is unvalidated text, and the report average counts rejected reviews.
10. Program reviews can be duplicated and the list needs a login, so it cannot be shared.
11. Product and service `reviewer_name` can be an email address.

## 11. Decisions needed from the product owner

1. Approve first or publish first, per module? Today products, services and events are approve first, and trainings and programs are publish first. Approve first is only workable once the queue and notifications exist.
2. How is the reviewer shown: full name, first name and initial, or anonymous with a verified badge?
3. Should an edited review need approval again?
4. Should a learner be able to review right after enrolling, or only after some progress?
5. Should providers (staff) be able to delete reviews? They can today.
6. Should an event review require check-in? A confirmed registration includes people who never came.
7. What happens to reviews when a user account is deleted or an item is archived?
8. Is a stars-only review fine everywhere?
9. Will services get a booking record? Until then "verified" stays off for services.

## 12. Checks for QA

- Posting a review returns 201. A rating of 0, 6 or "five" returns 422.
- Posting twice from one account leaves a single training, product or service review, containing the second text.
- A learner who is pending, rejected, cancelled or waitlisted cannot review a training (403).
- A new product review is missing from the public list. After approval it appears and the average changes. After rejection it disappears.
- A product with no approved reviews shows "No ratings yet".
- No screen shows a reviewer's email.
- A provider cannot approve or reject a product or service review (403). An Enterprise Admin can.
- An owner cannot touch another business's review. This fails today (problem 5).
- Timestamps show in the viewer's local time.
