# Event push notifications (mobile app)

Same pattern as Trainings: the app routes on `category` + ids in the FCM **data** map. Every value in `data` is a **string**, nothing is nested, nothing is
`null`. The same values are in the in-app feed (`GET /api/v1/notifications`) and the Socket.IO `notification` event (`metadata`).

Always present: `category`, `event_id`, `event_name`, `entity_type` (= `"event"`), `entity_id` (= `event_id`).
The notification block (`title`, `body`) is for display only: route on `data`.

## 1. Final category enum

### 1.1 Event lifecycle (participants)

| `category` | Sent when | To | Title | Body | Extra `data` keys |
|---|---|---|---|---|---|
| `event_new` | the event is published | users of the event's enterprise (tenant), not the publisher; once per event | New event | `{event_name} is now open` | |
| `event_registration_confirmed` | a registration is confirmed: free, paid (checkout), walk-in, or a free seat given from the waitlist | the registrant | Registration confirmed | You're registered for `{event_name}` | `registration_id` |
| `event_registration_cancelled` | the registrant or staff cancels one registration | the registrant | Registration cancelled | Your registration for `{event_name}` was cancelled | `registration_id` |
| `event_waitlist_joined` | someone joins the waitlist | that person | Waitlist joined | You're on the waitlist for `{event_name}` | `waitlist_id` |
| `event_waitlist_promoted` | a **paid** seat is offered to the next person (15 minutes) | that person | Spot available | A spot opened for `{event_name}`. Register now | `waitlist_id`, `expires_at` (UTC, `...Z`) |
| `event_reminder` | 24 h and 1 h before the start (scheduler), and the organiser's "send reminder" button | registered people (`confirmed`, `attended`) | Event reminder | `{event_name}` starts soon | `registration_id`, `starts_at` (UTC), `kind` (`24h`, `1h`, `manual`) |
| `event_updated` | date, time, time zone, venue or meeting link changed | registered people | Event updated | Details changed for `{event_name}` | `registration_id`, `starts_at`, `changed` (comma list: `start_date,venue,...`) |
| `event_cancelled` | the event is cancelled | registered and waitlisted people | Event cancelled | `{event_name}` has been cancelled | |
| `event_feedback_request` | the event completes (manually or automatically) | people who **attended** and have not sent feedback | Feedback | How was `{event_name}`? Share feedback | `registration_id` |

A **free** waitlist seat is given automatically as a confirmed registration, so that person gets `event_registration_confirmed` (not `event_waitlist_promoted`, which is only for the
paid offer). One push per person per action: someone with two registrations is notified once for event-level pushes (`event_updated`, `event_cancelled`, `event_reminder`, `event_feedback_request`).

### 1.2 Event Discussion (participants on mobile)

Only these five; a new post never notifies anyone by itself.

| `category` | Sent when | To | Body | Extra `data` keys |
|---|---|---|---|---|
| `event_discussion_reply` | someone replies to my message | the author of the parent message | Someone replied to your discussion message. | `message_id` (the reply), `parent_id` |
| `event_discussion_announcement` | the Enterprise Admin posts an announcement | everyone registered who can read the discussion (also people who never opened it) | New announcement in your event discussion. | `message_id` |
| `event_discussion_approved` | the Enterprise Admin approves my flagged message | the message author | Your discussion message has been approved. | `message_id` |
| `event_discussion_deleted` | the Enterprise Admin deletes / rejects my flagged message | the message author | Your discussion message was not approved. | `message_id` |
| `event_discussion_reply_nudge` | a reply is still unread after the interval | the author of the parent message | You have an unread reply in your event discussion. | `message_id`, `parent_id` |

All five also carry `status`, `occurred_at`, `deep_link` (a web path, ignore it on mobile), `author_name` (for reply and announcement) and, for a nudge, `nudge: "True"`.
A participant is only notified while they **still have access when it is sent** (active registration, or the event's Enterprise Admin): a person whose registration was cancelled meanwhile gets nothing.
Web / Enterprise Admin only (not for mobile): `event_discussion_flagged`, `event_discussion_pending_review`.

### 1.3 Event reviews (unchanged)

`review_approved`, `review_rejected`, `review_deleted` (and `review_pending`): `data` has `entity_type: "event"`, `event_id`, `entity_id`, `review_id`. Unchanged by this work.

## 2. Deep-link contract (frozen)

| `category` | Open |
|---|---|
| `event_new`, `event_registration_confirmed`, `event_registration_cancelled`, `event_waitlist_joined`, `event_reminder`, `event_updated`, `event_cancelled` | Event Details `/(main)/event/{event_id}` |
| `event_waitlist_promoted` | Register `/(main)/event/register?id={event_id}` (+ `waitlist_id`) |
| `event_feedback_request` | Feedback screen of `event_id` |
| `event_discussion_*` | Event Discussion of `event_id`, focus `message_id` |
| `review_approved`, `review_rejected`, `review_deleted` | Event Details with reviews focus (`review_id`) |

## 3. Sample payloads

**These are what the code produces in tests, not captured from staging or from Firebase** (nothing was sent to FCM; I have no access to staging). The reminder is the exact map the
delivery service hands to FCM in `tests/test_event_notifications.py::test_the_real_pipeline_hands_fcm_a_flat_string_map_with_the_collapse_key_outside_the_data`.

`event_registration_confirmed`
```json
{ "notification": { "title": "Registration confirmed", "body": "You're registered for Community Wellness Walk" },
  "data": { "category": "event_registration_confirmed", "event_id": "c4a4fed1-b8c8-4180-8461-7f6b22481206", "registration_id": "be954d8e-d443-437a-9ad8-7f96a7a77f16",
            "event_name": "Community Wellness Walk", "entity_type": "event", "entity_id": "c4a4fed1-b8c8-4180-8461-7f6b22481206" } }
```
`event_reminder`
```json
{ "notification": { "title": "Event reminder", "body": "Community Wellness Walk starts soon" },
  "data": { "category": "event_reminder", "event_id": "c4a4fed1-b8c8-4180-8461-7f6b22481206", "event_name": "Community Wellness Walk", "entity_type": "event",
            "entity_id": "c4a4fed1-b8c8-4180-8461-7f6b22481206", "registration_id": "be954d8e-d443-437a-9ad8-7f96a7a77f16",
            "starts_at": "2026-11-20T09:00:00Z", "kind": "24h" } }
```
`event_discussion_reply` (built from the code path, values illustrative)
```json
{ "notification": { "title": "New reply", "body": "Someone replied to your discussion message." },
  "data": { "category": "event_discussion_reply", "event_id": "<uuid>", "event_name": "Health Harmony Summit", "entity_type": "event", "entity_id": "<same as event_id>",
            "message_id": "<reply uuid>", "parent_id": "<parent uuid>", "status": "published", "occurred_at": "2026-11-20T09:05:00Z",
            "author_name": "Ravi", "deep_link": "/events/<uuid>/discussion?message=<reply uuid>" } }
```
`starts_at` is UTC: the event's stored start is local time in its `time_zone` (for example 09:00 in `Asia/Kolkata` is `03:30:00Z`).

## 4. What was already sending, and what changed

Before this change the Event module sent these (push reached a phone only if the person could be found in the legacy `chat_users` table by email; the data had no `category`, `event_name` or `entity_*`):

| Action | Sent before | Now |
|---|---|---|
| Registration confirmed | `booking_confirmed` | `event_registration_confirmed` (email unchanged) |
| One registration cancelled | `event_cancelled` (same name as the whole event) | `event_registration_cancelled` |
| Event cancelled | `event_cancelled`, registered only | `event_cancelled`, registered **and waitlisted** |
| Schedule change | `event_schedule_change` for any field change | `event_updated`, only for date / time / time zone / venue / meeting link (the email still goes out for any change) |
| Paid seat offered | `event_waitlist_offer` | `event_waitlist_promoted` (the offer email is unchanged) |
| Paid checkout | `payment_success` push | `event_registration_confirmed` push; the payment email is unchanged |
| Reminder | only the organiser's button | scheduler 24 h and 1 h before + the button |
| Event published, waitlist joined, feedback request | nothing | `event_new`, `event_waitlist_joined`, `event_feedback_request` |
| Discussion | `event_discussion_message_approved`, `_message_deleted`, `_unread_reply`; `entity_type: "event_discussion_message"` | `event_discussion_approved`, `_deleted`, `_reply_nudge`; `entity_type: "event"`, `entity_id` = event id |

**If the web app already reads the three old discussion category names or `entity_type: "event_discussion_message"`, tell me and I will keep both.** Older feed rows keep their old names.

Now every one is delivered to the person's platform account: the registration's saved `user_id` (new on registrations and waitlist entries, see section 6), the same email on another registration,
then the legacy email lookup.

## 5. Delivery rules

1. Routing fields are in `data`; all values are strings; `None` values are dropped. Free text such as `reason` is never in the push.
2. The user's **push preference** and registered devices are honoured by the platform pipeline (`push_enabled`, active device tokens).
3. **Quiet hours are not enforced**: the preference stores a bare `HH:MM` with no time zone, so there is nothing to compare it with. Say if you want a time zone added to the preference.
4. **No duplicates**: each delivery is claimed once (a retry or a second worker sends nothing); the old in-app/push path of the same action is switched off (email stays), so a registration does not
   produce both `event_registration_confirmed` and a generic update. A rescheduled event is reminded again for its new start.
5. **One reminder per event on the phone**: reminders carry a collapse key `event_reminder:{event_id}` (Android collapse key + tag, iOS thread id), so the 1 h reminder replaces the 24 h one. It is not part of `data`.
6. Sent after the change is committed, from a background thread with its own database session; it can never fail the request that caused it.

## 6. Settings, migrations, rollout

* `EVENT_REMINDERS_ENABLED` (default: on in production, off elsewhere), `EVENT_REMINDER_HOURS` (default `24,1`; empty = none), `EVENT_REMINDER_INTERVAL_SECONDS` (default 300).
  The scheduler is an in-process thread like the Training one (there is no Celery beat here); running it in every worker is harmless because deliveries are claimed.
* Migration `o4f7b0d3e8a5` (after `n3e6a9c2d7f4`) adds `event_waitlist.user_id`. `event_registrations.user_id` comes from `n3e6a9c2d7f4` (see `group-chat-event.md`);
  run `python -m scripts.backfill_event_registration_user_ids --apply` so older registrations can be reached. Registrations the backfill cannot link stay silent until the person opens "my registrations".
* Rollback: redeploy the previous build (the new columns are ignored); `alembic downgrade` drops them. Nothing else is stored.

## 7. Not done / not tested

* Not tested against Firebase, staging or a device: the pipeline was run with FCM replaced by a capture. Tests: `tests/test_event_notifications.py` (20), `tests/test_event_discussions.py` (25), on SQLite.
* `event_new` goes to the enterprise's users from the identity service (the same audience as `training_new`); if the identity service is not configured nobody is told (a log line says so). It is not sent to users of other enterprises.
* A registration cancelled by a **refund approval** sends the refund notification as before, not `event_registration_cancelled`.
* `event_feedback_request` goes out when the event is marked completed (manually or by the auto-complete sweep), not after a delay.
* Walk-in attendees are notified only if their email matches a platform account.
* `email_enabled` / `in_app_enabled` preferences are not consulted for these pushes (as for the other automatic notifications).
