# Training notifications

All Training notifications appear in `GET /users/me/notifications` (and as push, when the user has a
device token). Two triggers also send an email. Code: `app/services/training_notifications.py`.

| # | Trigger | `category` | In-app / push | Email | `metadata` keys |
|---|---------|-----------|:---:|:---:|---|
| 1 | Admin approves an enrolment | `enrolment_approved` | yes | **yes** | `training_id`, `enrolment_id`, `status` |
| 2 | A new training is published | `training_new` | yes | no | `training_id` |
| 3 | Learner completes the training (certificate earned) | `training_certificate` | yes | **yes** | `training_id`, `certificate_url` |
| 4 | Admin posts an announcement | `training_announcement` | yes* | only if channel is `email`/`both` | `training_id`, `announcement_id` |
| 5 | Admin answers a learner's question | `training_answer` | yes | no | `training_id`, `discussion_id` |
| 6 | One day before the training starts | `training_reminder` | yes | no | `training_id`, `kind`, `date` |
| 7 | On the final day of the training | `training_final_day` | yes | no | `training_id`, `kind`, `date` |

Already existing, now delivered through the same path: `training_enrolment_confirmation`
(enrolled / pending approval), `enrolment_rejected`, `enrolment_cancelled`.

\* Announcements honor the `channel` the admin picks: `in_app` (default) = inbox only, `email` = email only,
`both` = inbox and email. `sms` is not implemented and is treated as `in_app`.

## Payloads and routing

Every notification carries `metadata.category` plus the ids below, identically in the inbox
(`GET /users/me/notifications` → `items[].metadata`), the socket `notification` event, and the push
`data` block (where every value is a string). Keys with no value are omitted, never `null`/`"None"`.
Route on `category`; `training_id` is always present.

| `category` | Extra `metadata` keys | Open |
|---|---|---|
| `training_enrolment_confirmation` | `enrolment_id`, `status` (`enrolled`\|`pending_approval`) | training detail |
| `enrolment_approved` | `enrolment_id`, `status` | training / learning screen |
| `enrolment_rejected` | `enrolment_id`, `status`, `reason`? | training detail (shows reason) |
| `enrolment_cancelled` | `enrolment_id`, `status` | training detail |
| `training_new` | — | training detail |
| `training_certificate` | `certificate_url` | training certificate |
| `training_announcement` | `announcement_id` | training announcements |
| `training_answer` | `discussion_id` | training Q&A thread |
| `training_reminder` | `kind`, `date` (start date) | training detail |
| `training_final_day` | `kind`, `date` (end date) | training detail / learning screen |

## Who gets what

- **Approval, certificate, answer:** the one learner concerned.
- **Announcements, reminders, final-day:** learners whose enrolment status is `enrolled` or `attended`
  (not pending, rejected, cancelled or waitlisted).
- **New training:** every user of the training's tenant (via the Invigorate internal API
  `INVIGORATE_AUTH_BASE_URL` + `INVIGORATE_INTERNAL_API_KEY`), except the person who published it.
  Sent only on the **first** publish — republishing after an unpublish/suspend does not notify again.
  If the internal API is not configured, nothing is sent (logged).
- **Answer:** only when a staff user (`admin`, `provider`, `super_admin`) replies; a peer reply, or the
  asker answering themselves, does not notify. Questions posted before this release have no stored
  user id and are matched by email.

## Reminders (6 and 7)

- Judged in the **training's own `time_zone`**, not UTC. Sent from **09:00 local** on the day:
  day-before = the calendar day before `start_date`; final-day = the calendar day of `end_date`
  (or `start_date` when there is no `end_date`).
- Only `published`, non-deleted trainings with dates.
- No Celery worker/beat is deployed, so a daemon thread in each API process ticks every
  `TRAINING_REMINDER_INTERVAL_SECONDS` (default 900). Each reminder is first *claimed* in
  `training_notification_log` (unique per training + learner + kind + date), so running in several
  workers or restarting never sends twice. Rescheduling a training to a new date re-arms its reminders.
- `TRAINING_REMINDERS_ENABLED`: unset = **on in production, off in development**; set `true`/`false` to override.

## Delivery rules

- Email is sent directly over SMTP (`email_user` / `email_pass`) exactly once; the inbox pipeline is
  called with `in_app`/`push` only, so there are no duplicate emails.
- The recipient's inbox is found from `training_enrolments.user_id` (set when a learner enrols themselves)
  and otherwise looked up by email. Enrolments created by an admin on someone's behalf, group members and
  checkout enrolments have no `user_id` yet and rely on the email lookup — if that lookup finds no user,
  the in-app notification is skipped (logged) but any email is still sent.
- Delivery runs on a background thread with its own DB session; a failure never breaks or slows the API call.

## Deploy

Run `alembic upgrade a7c3e91d4b52` — adds `training_enrolments.user_id` and `training_notification_log`.
