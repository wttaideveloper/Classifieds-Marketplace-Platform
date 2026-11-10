# Event Discussions API

For the web and mobile developers. Base path `/api/v1`. All times are UTC. Every route needs a signed-in user.

Each event has one discussion. Registered participants and the event's Enterprise Admin post messages and replies. **Every message is checked by AI before anyone else can see it.** Flagged messages wait for an Enterprise Admin. Extreme messages are blocked. The Super Admin can watch, never act.

## 1. Who the caller is

The caller's role is worked out per event; nothing is sent.

| Role | Who | Can |
|---|---|---|
| `participant` | a user with a **confirmed or attended** registration for the event (matched by the e-mail on the registration) | read, post, reply, edit or appeal their own blocked messages, see their own messages' status |
| `enterprise_admin` | the Enterprise Admin of the event's owning tenant | everything a participant can, plus announcements, the review list, approve / delete flagged messages, the moderation history |
| `super_admin` | an active Platform Super Admin | **read only**: messages, review lists, moderation history and activity across enterprises. Posting, replying, editing, appealing or deciding is 403 `SUPER_ADMIN_READ_ONLY` |
| nobody | everyone else | the Discussion tab is locked |

A cancelled or no-show registration, or a registration that was never made, gives no access. Cancelling a registration revokes access at once.
An Enterprise Admin of another business has no access (`NOT_EVENT_ADMIN`).

## 2. How a message moves

```
post -> pending_moderation --AI--> published          (safe)
                                 -> pending_review     (flagged)  --Enterprise Admin--> published (approve) | deleted (delete)
                                 -> blocked            (extreme)  --author edits--> checked again   |  --author appeals--> pending_review
        AI unavailable: stays pending_moderation, never published, checked again by a background job
```

| Status | Who sees it | Author is told |
|---|---|---|
| `pending_moderation` | the author only | "Your message is being checked and will appear once it passes moderation." |
| `published` | everyone with access | nothing |
| `pending_review` | the author, and the Enterprise Admin (review list) | "Your message is pending admin review." |
| `blocked` | the author only | "Your message violates community guidelines and cannot be posted." |
| `deleted` | the author only (as "not approved") | "Your message was not approved." |

A blocked message is never published or broadcast and sends no notification. Other people asking for a message that is `deleted`, blocked or flagged get 404 `MESSAGE_UNAVAILABLE`, "This message is no longer available."

## 3. Endpoints

Path prefix for event routes: `/events/{event_id}/discussion`.

| Method and path | Who | What |
|---|---|---|
| `GET /access` | any signed-in user | role, `can_read`, `can_post`, `can_moderate`, `is_closed`, `locked_reason`, `message`. Call it when Event Details opens: `can_read: false` keeps the Discussion tab locked |
| `GET /messages?parent_id=&order=oldest\|newest&page=&page_size=` | participant, admin, Super Admin | top-level messages (or the replies of `parent_id`). Announcements first. You also see your own non-published messages |
| `POST /messages` | participant, Enterprise Admin | `{body, parent_id?, is_announcement?}`; 201 with the outcome |
| `GET /messages/mine?status=` | participant, admin | my own messages and their status |
| `GET /messages/{id}` | participant, admin, Super Admin | the message with its replies. The target of a notification. Marks a reply to you and its notifications as read |
| `PUT /messages/{id}` | the author | edit a `blocked` message; it is checked again |
| `POST /messages/{id}/appeal` | the author | ask for a `blocked` message to be reviewed; one appeal per message |
| `POST /messages/{id}/decision` | Enterprise Admin | `{action: "approve" \| "delete", note?}` for a `pending_review` message |
| `GET /moderation/queue?status=pending_review` | Enterprise Admin, Super Admin (read) | flagged messages of this event, with the event name, sender, content, time, AI reason and status. `pending_count` is the number waiting |
| `GET /moderation/history?action=&message_id=` | Enterprise Admin, Super Admin (read) | every step of every message |
| `GET /event-discussions/flagged?event_id=&tenant_id=&status=` | Enterprise Admin (own events), Super Admin (all, read) | the flagged-messages screen across events |
| `GET /event-discussions/activity?tenant_id=` | Enterprise Admin, Super Admin (read) | one row per event: counts by status and the last activity |

Lists use `page` and `page_size` and answer `{items, pagination: {total, page, page_size, total_pages}}`.

### Posting

```json
POST /events/{event_id}/discussion/messages
{ "body": "Is parking available at the venue?", "parent_id": null }
```

```json
201 {
  "message": { "id": "…", "event_id": "…", "parent_id": null, "root_id": null, "body": "Is parking available at the venue?",
               "author": { "id": "…", "name": "Priya", "role": "participant" }, "is_announcement": false, "status": "published",
               "status_message": null, "is_mine": true, "is_appeal": false, "reply_count": 0, "is_unread_reply": false,
               "created_at": "2026-11-09T10:00:00", "published_at": "2026-11-09T10:00:00" },
  "user_message": null
}
```

`message.status` tells what happened and `user_message` is the sentence to show the sender (`null` when published). For a flagged message `status` is `pending_review`; for an extreme one `blocked`; if the AI could not be reached `pending_moderation`.

- Plain text only, up to 2000 characters (`EVENT_DISCUSSION_MAX_CHARS`). Show it as text, not HTML.
- Replies are one level deep: a reply to a reply joins the same thread (`root_id`). You can only reply to a published message.
- `is_announcement` is for Enterprise Admins and top-level messages only; announcements are listed first and notify the participants.
- The same text from the same person within 30 seconds is 409 `DUPLICATE_MESSAGE`.
- Enterprise Admins' messages are checked like everyone else's.

### Review item (`/moderation/queue`, `/event-discussions/flagged`)

```json
{ "message_id": "…", "event_id": "…", "event_name": "Health Harmony Summit", "sender_id": "…", "sender_name": "Priya", "sender_role": "participant",
  "body": "…", "status": "pending_review", "is_appeal": false, "flagging_reason": "AI flagged: harassment", "submitted_at": "2026-11-09T10:00:00",
  "decided_by_name": null, "decided_at": null, "decision_note": null, "parent_id": null }
```

### Moderation history item

`{id, event_id, message_id, action, from_status, to_status, label, reason, actor_id, actor_name, actor_role, created_at}`.
`action`: `submitted`, `auto_published`, `flagged`, `blocked`, `ai_unavailable`, `approved`, `deleted`, `edited_resubmitted`, `appealed`. `actor_role` is `system` for the AI. History is never changed or removed, and includes blocked messages.

## 4. Notifications and where they open

They use the existing notification feed and push, and are sent after the change is saved. Each has `deep_link`, the screen it opens for that reader, plus `event_id`, `event_name`, `message_id`, `parent_id`, `status` and `occurred_at` in the metadata.

| Category | To | When | Text | Opens |
|---|---|---|---|---|
| `event_discussion_reply` | the author of the message replied to (not yourself) | a reply is published | "Someone replied to your discussion message." (to an Enterprise Admin: "A participant replied to your discussion.") | the exact reply (participant link, or the admin discussion link) |
| `event_discussion_announcement` | participants known to the discussion | an announcement is published | "New announcement in your event discussion." | the announcement |
| `event_discussion_approved` | the sender | an Enterprise Admin approved it | "Your discussion message has been approved." | the message |
| `event_discussion_deleted` | the sender | an Enterprise Admin deleted it | "Your discussion message was not approved." | the message's status |
| `event_discussion_flagged` | the event's Enterprise Admins | the AI flagged a message, or an appeal arrived (`metadata.is_appeal`) | "A discussion message requires your review." | the exact flagged message |
| `event_discussion_pending_review` | the event's Enterprise Admins | a nudge (section 5) | "You have N flagged discussion messages waiting for review." | the event's flagged list |
| `event_discussion_reply_nudge` | the author of the message replied to | a nudge (section 5) | "You have an unread reply in your event discussion." | the exact reply |

The links are set on the server and **the web and mobile teams must confirm the real routes**, they are placeholders until then (`{event_id}` and `{message_id}` are replaced):

| Setting | Default |
|---|---|
| `EVENT_DISCUSSION_LINK_PARTICIPANT` | `/events/{event_id}/discussion?message={message_id}` |
| `EVENT_DISCUSSION_LINK_ADMIN_MESSAGE` | `/enterprise/events/{event_id}/discussion?message={message_id}` |
| `EVENT_DISCUSSION_LINK_ADMIN_FLAGGED` | `/enterprise/events/{event_id}/discussion/flagged?message={message_id}` |
| `EVENT_DISCUSSION_LINK_ADMIN_QUEUE` | `/enterprise/events/{event_id}/discussion/flagged` |

What the app should do on a tap: if logged out, log in first and then continue to the link. Then call `GET /access` (or just `GET /messages/{id}`): 403 `DISCUSSION_LOCKED` means access was lost (registration cancelled), 404 `EVENT_NOT_FOUND` means the event is unavailable, 404 `MESSAGE_UNAVAILABLE` means "This message is no longer available." Opening the message with `GET /messages/{id}` marks its notifications as read.

Rules: no notification for every message, only the cases above; a reply to yourself notifies nobody; a blocked message is never broadcast; each notification is sent once (a retry cannot duplicate it). The AI's reason is only in what the Enterprise Admin receives.
The Enterprise Admin's notification goes to Enterprise Admins the platform knows for the event: those who have opened its discussion, plus the owning tenant's admins from the identity service when that lookup answers.

## 5. Nudges

| Nudge | Trigger | Stops when |
|---|---|---|
| Unread reply (to the person replied to) | a published reply stays unopened for `EVENT_DISCUSSION_UNREAD_NUDGE_HOURS` (12) | they open the message. Sent once per reply |
| Pending flagged messages (to the Enterprise Admins) | flagged messages have waited `EVENT_DISCUSSION_PENDING_NUDGE_HOURS` (24); again after the same wait, at most `EVENT_DISCUSSION_PENDING_NUDGE_MAX` (3) times | the queue is empty (the counter then restarts) |

They run in a background job every `EVENT_DISCUSSION_JOBS_INTERVAL_SECONDS` (300). Quiet hours: set `EVENT_DISCUSSION_QUIET_START_HOUR` and `..._END_HOUR` (UTC, -1 = off); nudges wait for the next round. The 24-hour interval is the suggested starting value and needs client confirmation. The same job judges again the messages left `pending_moderation` because the AI was down (up to 20 tries).

## 6. AI moderation

Two layers. (1) Word lists the client maintains for Tamil, Tanglish and local slang: `EVENT_DISCUSSION_BLOCK_WORDS` (blocked at once) and `EVENT_DISCUSSION_FLAG_WORDS` (sent to review), whole words, any letter case. (2) The OpenAI moderation endpoint (`EVENT_DISCUSSION_MODERATION=openai`, needs `OPENAI_API_KEY`): severe categories (threats, hate with threats, sexual content involving minors, graphic violence, self-harm instructions) at or above `EVENT_DISCUSSION_BLOCK_THRESHOLD` (0.85) are extreme; anything flagged or scoring at least `EVENT_DISCUSSION_FLAG_THRESHOLD` (0.4) goes to review; the rest is safe.

**If the AI cannot answer, or `EVENT_DISCUSSION_MODERATION=off`, or no key is set, nothing is published**: messages stay `pending_moderation`. `rules` uses only the word lists. Set `OPENAI_API_KEY` before launch or no message will ever appear.
Tamil and Tanglish coverage of the AI model is not verified; the word lists are the backstop and the client must supply them.

## 7. Errors

`detail` is a sentence; `code` is for the program. Validation failures also have `errors: [{field, code, message}]`.

| Status | `code` | When |
|---|---|---|
| 401 | | not signed in |
| 403 | `DISCUSSION_LOCKED` (`reason`: `NOT_REGISTERED`, `REGISTRATION_CANCELLED`, `NOT_EVENT_ADMIN`) | no access to this discussion |
| 403 | `SUPER_ADMIN_READ_ONLY` | a Super Admin tried to post, reply, edit, appeal or decide |
| 403 | `FORBIDDEN` | a participant asking for the review list or history, or deciding |
| 403 | `SELF_REVIEW` | approving your own message (another Enterprise Admin must) |
| 403 | `NOT_AUTHOR`, `ANNOUNCEMENT_ADMIN_ONLY` | editing or appealing someone else's message; a participant making an announcement |
| 404 | `EVENT_NOT_FOUND` | the event is unavailable |
| 404 | `MESSAGE_UNAVAILABLE` | "This message is no longer available." Deleted, never published, or not yours to see |
| 409 | `DISCUSSION_CLOSED` | the event is completed, cancelled or archived: still readable, no new posts |
| 409 | `PARENT_NOT_AVAILABLE` | replying to a message that is not published |
| 409 | `DUPLICATE_MESSAGE` | the same text again within 30 seconds |
| 409 | `INVALID_STATUS`, `ALREADY_APPEALED` | the action does not fit the message's status |
| 422 | `VALIDATION_FAILED`, `MESSAGE_TOO_LONG` | empty or too long |

## 8. Decisions made, and to confirm with the client

- **Super Admin is read only** (as the flow recommends); this needs the client's approval.
- **A closed discussion** means the event is `completed`, `cancelled` or `archived`. The flow does not say how a discussion is closed.
- **An Enterprise Admin cannot approve their own flagged message.** Another Enterprise Admin of the event must, or they can delete it. With one admin, an own flagged message stays pending. Say if you want self-approval allowed.
- **Appeal** (recommended in the flow) is built: a blocked message goes to the Enterprise Admins for review, once.
- **Editing** is allowed only for blocked messages (to fix and retry). There is no edit or delete of a published message by its author.
- **Announcements** notify participants who have opened the discussion, because a registration only holds an e-mail address; the user id is learned when someone first opens the discussion.
- **Notification preferences** of a user are not consulted by these notifications (the platform has no per-category preference setting yet). Quiet hours only apply to nudges.

## 9. Not verified

- Not run against PostgreSQL, a real OpenAI key, or live push. Tests use an in-memory database and a stubbed AI.
- The migration `i6e1a4d9f3c5` (after `h5d0f3c8e2b4`) is checked offline only (`alembic upgrade … --sql`).
- Nothing about this module is deployed.
