# Group Chat: API Guide for Web and Mobile

For the web and mobile developers. Base URL: `https://chat.wisdomtooth.tech/api/v1`. Swagger: `/docs` (tag "Group Chat").

This guide tells you which call to make on which screen, what comes back, what the server decides for you, and what the app has to handle itself. The rules behind the feature (who may do what, consent, audit) are in `group-chat-api.md`; read that for the full tables.


## 1. Basics

**Authentication.** Same as every other API.
- Mobile: `Authorization: Bearer <access token>` on every call.
- Web: either the Bearer token, or the session cookie with `credentials: "include"`.
- A missing or expired token gives `401`.

**Format.** JSON. Ids are UUID strings. Times are UTC without a "Z", for example `2026-10-12T10:41:29.770727`. Add the "Z" before you parse them, then show local time.

**Errors.** `{"detail": "message"}`. Status codes:

| Status | What it means for the app |
|---|---|
| 401 | sign in again |
| 403 | you cannot do this. Often "you are not in this group": remove the group from the list and leave the screen. Otherwise show `detail` |
| 404 | the group, message or invitation is gone, or the message is from before you joined |
| 409 | the state does not allow it: group closed, group full, owner must hand over first |
| 422 | the input is wrong: show `detail` next to the field (name with a patient's name, consent not ticked, guardian missing, reason missing) |

**Lists.** Message lists use a cursor (`next_cursor`). Group lists use `page` and `page_size`.

**There is no idempotency key on send.** If a send times out, do not resend blindly: load the latest messages first and check whether it arrived, otherwise the message can be posted twice.


## 2. Two kinds of group

The server tells you which one you are in with `kind`. Several screens change with it.

| | `care` group | `program` group |
|---|---|---|
| Meaning | one patient's care team (up to 15) | several patients in one program (up to 50) |
| Names a patient sees | everyone in full | providers in full, other patients by display name |
| "Seen by" | shown to everyone | patients never see it, providers do |
| Typing indicator | yes | no (the server refuses it) |
| Reactions | none | a small set |
| Providers-only posting | off by default | on by default |
| Patients in the list | Groups tab | Groups tab |

Read `limits` in the group response for the live numbers instead of hard-coding them.


## 3. Screens and the calls behind them

### Patient

| Screen | Calls |
|---|---|
| **Groups tab** (list) | `GET /groups` (add `?archived=true` for the archived ones). Each item has `unread_count`, `last_message_preview`, `muted`, `pinned`, `kind`. Pinned groups come first |
| **Invitations** (badge and list) | `GET /groups/invitations`. Also arrives as a notification and the socket event `group_invitation` |
| **Invitation detail** | `GET /groups/invitations/{id}`. Show every field. Then `POST /groups/{id}/accept` or `POST /groups/{id}/decline` |
| **Group chat** | `GET /groups/{id}` (header, notices), `GET /groups/{id}/messages` (history), `POST /groups/{id}/messages` (send), `POST /groups/{id}/read` |
| **Group info / members** | `GET /groups/{id}/members`, `PATCH /groups/{id}/preferences` (mute, pin), `POST /groups/{id}/leave`, `POST /groups/{id}/consent/withdraw` |
| **Message actions** | edit `PATCH …/messages/{mid}`, retract `POST …/retract`, react `PUT …/reactions`, report `POST /groups/{id}/reports` |
| **Search / files** | `GET /groups/{id}/search?q=`, `GET /groups/{id}/files` |
| **My privacy** | `GET /groups/consents/me`, `POST /groups/data-requests`, `GET /groups/grievance-officer` |

### Provider

Everything above, in the same inbox as 1:1 chats (`GET /conversations` includes groups, marked `conversation_type: "group"` with `group_kind`; the group name is in `subject`; open the group screen with `GET /groups/{id}`). Plus:

| Screen | Calls |
|---|---|
| **Create group** | `POST /groups` |
| **Group settings** | `PATCH /groups/{id}` (name, description, picture), `PATCH /groups/{id}/settings` (providers-only posting), `POST /groups/{id}/close`, `POST /groups/{id}/reopen` |
| **Manage members** | `POST /groups/{id}/members` (invite), `DELETE /groups/{id}/members/{user}?reason=` (remove), `PATCH …/members/{user}/role` (admin or member), `POST` and `DELETE …/members/{user}/mute-posting` |
| **Moderation** | `POST …/messages/{mid}/hide` (with a reason), `PUT` and `DELETE …/messages/{mid}/pin`, `GET /groups/{id}/pinned` |
| **Care record** | `POST …/messages/{mid}/save-to-record`, `GET /groups/{id}/clinical-records` |
| **Hand over** | `POST /groups/{id}/transfer-ownership` |
| **Audit and originals** (owner) | `GET /groups/{id}/audit`, `GET …/messages/{mid}/original` |

### Clinic admin and Super Admin (compliance)

| Screen | Calls |
|---|---|
| **All groups** | `GET /groups/compliance/groups` (filters `kind`, `status`, `legal_hold`). Details only, never messages |
| **Reports** | `GET /groups/reports`, `PATCH /groups/reports/{id}` |
| **Group audit** | `GET /groups/{id}/audit` |
| **Legal hold** | `POST` and `DELETE /groups/{id}/legal-hold` |
| **Reassign owner** | `POST /groups/{id}/reassign-owner` |
| **Remove or demote an admin** | `DELETE …/members/{user}?reason=`, `PATCH …/members/{user}/role` with a reason |
| **Verified providers** | `POST /groups/verified-providers`, `GET /groups/verified-providers`, `DELETE /groups/verified-providers/{user_id}` |


## 4. The group object

`GET /groups`, `GET /groups/{id}`, and the responses of create, accept, close, reopen, settings and preferences all return this shape.

```json
{
  "id": "6918ddf8-e923-4bd1-8067-84c444842799",
  "name": "Yoga batch Oct 2026",
  "kind": "program",
  "description": "Weekly yoga for beginners",
  "avatar_url": null,
  "context_type": "program",
  "context_id": "e7ef5b55-c305-4dea-b4fe-47119768f2b1",
  "status": "open",
  "admins_only_posting": true,
  "member_count": 3,
  "visibility_notice": "3 people can see this",
  "emergency_notice": { "text": "Not for emergencies. In an emergency call 112.", "number": "112" },
  "forwarding_blocked": true,
  "screenshots_blocked": true,
  "limits": { "max_members": 50, "max_admins": 3, "edit_window_minutes": 15 },
  "my_role": "member",
  "my_kind": "patient",
  "muted": false,
  "pinned": false,
  "is_archived": false,
  "unread_count": 2,
  "last_message_at": "2026-10-12T10:41:30.008326",
  "last_message_preview": "Thanks, see you then",
  "closed_at": null,
  "closed_reason": null,
  "retention_started_at": null,
  "retention_until": null,
  "legal_hold": false
}
```

How to use the fields:

| Field | What the app does with it |
|---|---|
| `visibility_notice`, `emergency_notice` | show on **every** group screen and on the invitation |
| `status` | `closed` means read-only: hide the message box, show "This group is closed" |
| `admins_only_posting` and `my_kind` | the message box is available only if `admins_only_posting` is false or `my_kind` is `provider` |
| `my_role` | `owner` or `admin`: show the manage controls. `member`: do not |
| `my_kind` | `provider`: can pin, can tag everyone, can save to record. `patient`: cannot |
| `kind` | switches the rules in section 2 |
| `unread_count` | the badge. Counts other people's messages since you last read, never your own, never system lines |
| `last_message_preview` | the list row text. It never shows a message that was removed |
| `muted` | show the muted icon. It never removes access |
| `screenshots_blocked`, `forwarding_blocked` | block screenshots where the phone allows it, and offer no "forward" action |


## 5. Calls, with the fields that matter

### Create a group (provider)

`POST /groups`
```json
{
  "name": "Yoga batch Oct 2026",
  "kind": "program",
  "description": "Weekly yoga for beginners",
  "avatar_url": "https://…/icon.png",
  "context_type": "program",
  "context_id": "e7ef5b55-c305-4dea-b4fe-47119768f2b1",
  "invitees": [
    { "user_id": "…" },
    { "user_id": "…", "display_name": "Ravi K.", "is_minor": true }
  ],
  "admins_only_posting": null
}
```
- `kind` is `care` or `program`. `context_type` is `program`, `appointment_series` or `care_episode`. All three of `name`, `kind` and the link are required.
- `is_minor: true` means the invitation will need a parent's or guardian's consent.
- Response `201`: the group object. Nobody invited is a member yet.
- Errors: `403` patient or unverified provider, `409` too many people, `422` missing link, name with a patient's name or a health condition.
- Upload the picture first with the attachment API and pass the link as `avatar_url`.

### Invitations

`GET /groups/invitations` → `{ "items": [InvitationResponse], "total": 1 }`

`GET /groups/invitations/{id}`
```json
{
  "conversation_id": "…",
  "name": "Yoga batch Oct 2026",
  "kind": "program",
  "description": "Weekly yoga for beginners",
  "purpose": "This is a program group for several patients taking part in one program.",
  "invited_by_name": "Dr Rao",
  "provider_names": ["Dr Rao"],
  "member_count": 1,
  "visibility_notice": "1 person can see this",
  "what_members_can_see": "Everyone in the group can read every message and file posted after they join. …",
  "emergency_notice": { "text": "Not for emergencies. In an emergency call 112.", "number": "112" },
  "requires_guardian_consent": false,
  "invited_at": "2026-10-12T10:41:29.771712"
}
```
Show all of it. If `requires_guardian_consent` is true, also ask for the parent's or guardian's name and relationship.

`POST /groups/{id}/accept`
```json
{ "consent": true, "display_name": "Priya S.", "guardian": { "name": "Meena Kumar", "relationship": "mother" } }
```
`consent` must be `true` (otherwise `422`). `guardian` only for minors. `display_name` is optional: other patients see it in a program group; if you leave it out the server uses first name and the initial of the last name. Response: the group object. Messages are visible from this moment on, not before.

`POST /groups/{id}/decline` `{ "reason": "optional" }` → `{ "status": "declined" }`

### Members

`GET /groups/{id}/members` → `{ "members": [...], "total": 3 }`, each member:
```json
{ "user_id": "…", "name": "Ravi K.", "role": "member", "kind": "patient", "status": "active",
  "is_me": false, "joined_at": "…", "posting_muted": false, "posting_muted_until": null }
```
- Use `name` exactly as returned. It already follows the privacy rules for the person asking: a patient sees other patients by display name in a program group. Never build a name from anywhere else.
- Admins also get members with `status: "invited"` (pending).
- `posting_muted` is filled in for admins only.

`POST /groups/{id}/members` `{ "invitees": [{ "user_id": "…" }] }` → `201 { "invited": [...], "skipped": [...] }`. People already in or already invited are skipped.

`DELETE /groups/{id}/members/{user_id}?reason=…` → `{ "status": "removed" }`. A `reason` is required to remove an admin. The owner cannot be removed (`403`).

`PATCH /groups/{id}/members/{user_id}/role` `{ "role": "admin" | "member", "reason": "…" }`. `reason` is required when demoting an admin. `422` if the person is not a verified provider; `409` if the group already has 3 admins.

`POST /groups/{id}/members/{user_id}/mute-posting` `{ "until": "2026-10-13T09:00:00", "reason": "…" }` (both optional). `DELETE` on the same path unmutes.

`POST /groups/{id}/leave` → `{ "status": "left" }`. The owner gets `409` until the group is handed over with `POST /groups/{id}/transfer-ownership` `{ "user_id": "…" }`.

### Messages

`GET /groups/{id}/messages?limit=50&cursor=…`
```json
{
  "items": [ /* oldest first */ ],
  "has_more": true,
  "next_cursor": "2026-10-12T10:41:30.008326|0b92cf4d-…",
  "unread_count": 2,
  "first_unread_message_id": "0b92cf4d-…",
  "pinned_count": 1
}
```
Each item:
```json
{
  "id": "…", "conversation_id": "…", "sender_id": "…",
  "sender_name": "Priya Sharma", "sender_kind": "patient",
  "message_type": "text",
  "content": "Thanks, see you then",
  "attachment_id": null,
  "status": "visible",
  "placeholder": null,
  "reply_to": { "id": "…", "sender_name": "Dr Rao", "preview": "Class moves to 7am", "is_hidden": false },
  "mentions": [], "mentions_all": false, "mentions_me": false,
  "is_edited": false, "edited_at": null,
  "pinned": false,
  "reactions": { "👍": 2 }, "my_reactions": ["👍"],
  "read_by": null,
  "created_at": "2026-10-12T10:41:30.008326"
}
```

| Field | Rendering |
|---|---|
| `message_type` | `text`, `image`, `document`, `audio`, `video`, or `system` (a centred line such as "Dr Rao added Priya") |
| `status` | `visible`, `retracted` or `removed_by_admin`. When it is not `visible`, `content` and `attachment_id` are empty: show `placeholder` ("Message retracted" or "Message removed by admin") in its place |
| `reply_to` | a quote above the message. If `is_hidden` is true there is no `preview` |
| `mentions_me` | highlight the message (you were tagged, or someone tagged everyone) |
| `is_edited` | show "edited" |
| `reactions`, `my_reactions` | counts per emoji, and which ones are yours |
| `read_by` | care groups: the ids who have seen it. Program groups: `null` |
| `sender_name` | already correct for the person reading |

Loading and reading:
1. Open the chat: `GET …/messages`. Put the "new messages" marker above `first_unread_message_id` (if it is not null).
2. Scroll up: call again with `cursor=next_cursor` while `has_more` is true.
3. When the person has seen the messages: `POST /groups/{id}/read`.

`POST /groups/{id}/messages`
```json
{ "content": "Thanks, see you then", "reply_to_id": "…", "mentions": ["<user id>"], "mention_all": false }
```
- Text: `content` (up to 4000 characters). File: upload with the attachment API first, then `{ "message_type": "image", "attachment_id": "…" }`. A message needs text or an attachment.
- `mentions` must be people in the group (`422` otherwise). `mention_all` is for providers only (`403` for a patient).
- Response `201`: the message object above.
- `403` means: you are not in the group, **an admin has muted you**, or **only providers can post right now**. Show `detail`.
- `409` means the group is closed.

`PATCH /groups/{id}/messages/{mid}` `{ "content": "…" }` edits your own text message, only within `limits.edit_window_minutes` of sending. After that: `403`. Hide the "Edit" action once the window has passed.

`POST …/messages/{mid}/retract` removes your own message from the group (at any time). `POST …/messages/{mid}/hide` `{ "reason": "…" }` is the admin version. Both return the message with `status` and `placeholder` set. Neither deletes anything.

`PUT …/messages/{mid}/pin` and `DELETE …/messages/{mid}/pin` (providers). `GET /groups/{id}/pinned` returns the pinned messages in the same shape as a message list.

`PUT …/messages/{mid}/reactions` `{ "emoji": "👍" }` → `{ "message_id", "reactions": {…}, "my_reactions": […] }`. `DELETE …/reactions?emoji=👍` takes it back. `GET /groups/{id}/reactions` → `{ "enabled": true, "emojis": [...] }`: build the picker from this. In a care group `enabled` is false and the calls return `403`.

`GET …/messages/{mid}/read-status` → `{ "seen_by_count": 4, "recipient_count": 6, "label": "seen by 4 of 6", "seen_by": ["…"] }`. A patient in a program group gets `403`: do not show it there.

`GET /groups/{id}/search?q=term` (at least 2 characters) → the message list shape. `GET /groups/{id}/files` → `{ "items": [{ "attachment_id", "message_id", "sender_id", "file_name", "mime_type", "file_size", "attachment_type", "created_at" }] }`. Both cover only what was sent after the person joined.

### Preferences (mute, pin, archive)

`PATCH /groups/{id}/preferences` `{ "muted": true, "muted_until": "2026-10-13T08:00:00", "pinned": true }`. Muting stops notifications but never access. To archive a group for yourself use the existing `PATCH /conversations/{id}/archive` `{ "archived": true }`; `GET /groups?archived=true` lists them.

### Close and reopen

`POST /groups/{id}/close` `{ "reason": "program ended" }` makes the group read-only. `POST /groups/{id}/reopen` `{ "reason": "…" }` needs the owner and a reason (`422` without one).

### Reports

`POST /groups/{id}/reports` `{ "message_id": "…", "reason": "privacy", "details": "…" }`. Leave out `message_id` to report the group. `reason`: `harassment`, `privacy`, `health_information`, `spam`, `inappropriate`, `safety`, `other`. Put "Report" in the message menu and the group menu.

### Privacy for the person

- `GET /groups/consents/me` → what they agreed to, per group, with `granted_at`, `withdrawn_at` and the invitation text (`notice`).
- `POST /groups/{id}/consent/withdraw` → they leave at once.
- `POST /groups/data-requests` `{ "conversation_id": "…" or null, "note": "…" }` → asks for a copy of their data. `GET /groups/data-requests/me` lists the requests.
- `GET /groups/grievance-officer` → `{ "name", "email", "phone", "configured" }`. When `configured` is false, say the contact is not set up yet.


## 6. Realtime (Socket.IO)

### Connecting

- **Web:** use the site's own origin; the cookie is the login.
  ```js
  const socket = io(window.location.origin, { path: "/api/socket.io", withCredentials: true });
  ```
- **Mobile:** pass the token.
  ```js
  const socket = io("https://chat.wisdomtooth.tech", { path: "/api/socket.io", auth: { token: accessToken } });
  ```
  The path is the server's `SOCKETIO_PATH`. It is `/api/socket.io` in the deployed environment and `.env.example`; the plain `/socket.io` is only the code default for local runs.

Join the room of the group you have open, and leave it when you close the screen:
```js
socket.emit("join_room", { conversation_id: groupId });   // answers "joined_room", or "error" if you are not an active member
socket.emit("leave_room", { conversation_id: groupId });
```
The server also puts your devices into the room when you accept, and takes them out when you are removed or leave.

Sending also works over the socket: `socket.emit("send_message", { conversation_id, content, message_type })` follows the same group rules. Use REST if you need `reply_to_id`, `mentions` or the rendered response.

### What to do when an event arrives

Events to the group room (payloads also carry `conversation_id`):

| Event | Payload | What the app does |
|---|---|---|
| `new_message` | `message` | add it to the list if this group is open, otherwise raise the unread count. The `message` is the same for everyone and **has no sender name**: look the name up in the cached member list |
| `message_updated` | `message` | replace the text, show "edited" |
| `message_hidden` | `message_id`, `status`, `placeholder` | replace that message with the placeholder |
| `message_pinned` | `message_id`, `pinned` | update the pin and the pinned count |
| `reaction_updated` | `message_id`, `reactions` | update the counts |
| `member_joined` | `user_id`, `role`, `kind` | refresh the member list, show the system line |
| `member_left` | `user_id` | refresh the member list |
| `member_removed` | `user_id` | refresh the member list |
| `member_role_changed` | `user_id`, `role` | refresh members; if it is you, refresh the group (your controls change) |
| `group_updated` | the changed fields | update the header or the message box (for `admins_only_posting`) |
| `group_closed`, `group_reopened` | | refresh the group, show or hide the message box |
| `typing` | `user_id`, `is_typing` | care groups only |

Events to your own room (`user:<id>`):

| Event | What the app does |
|---|---|
| `group_invitation` | refresh invitations and the badge |
| `member_removed` (with your `user_id`) | **close the open group screen** and remove the group from the list |
| `posting_muted`, `posting_unmuted` | disable or enable the message box; show "An admin has muted you" (`until` is set for a timed mute) |
| `notification` | the generic notification event, see section 7 |

Typing (care groups only): `socket.emit("typing_start", { conversation_id })` and `typing_stop`. To show a name for `typing`, use the member list. In a program group do not send it: the server answers with an error.

Refreshing the member list on each membership event is the simplest way to keep names right.


## 7. Notifications and deep links

Group notifications reveal nothing on the lock screen: the title is always **"New message"** and the body is empty. Do not add the group name, sender or text on the device unless the person chose that in the app's own settings.

| Where | How to route |
|---|---|
| **Push for a message** | `data.type == "group_message"`, with `data.conversationId` and `data.messageId`. Open the group chat screen for `conversationId` (Groups tab for patients) |
| **Push or feed notification, anything else** | `metadata.category` and `metadata.conversation_id` |
| Feed (`GET /users/me/notifications`) and the `notification` socket event | `metadata.category` is one of the values below |

| Category | Open |
|---|---|
| `group_invitation` | the invitation detail for `conversation_id` |
| `group_message` | the group chat (a tag has `mentioned: true` in the chat notification data; the text is still not shown) |
| `group_removed` | nothing to open: show "You were removed from a group" |
| `group_role_changed` | the group (`metadata.role` says which role) |
| `group_closed`, `group_reopened` | the group |
| `group_report_filed` (compliance) | the report (`metadata.report_id`) |

Muted groups send no push, and the unread count still grows. An @-tag in a **care** group still arrives while muted; in a program group it does not.


## 8. Rules the screens must follow

1. Show `visibility_notice` and `emergency_notice` on every group screen and the invitation.
2. Never show an email, phone number or any name the API did not give you. Patients are shown as the API returns them.
3. Patients: groups go in a separate **Groups** tab. Providers: groups share the inbox with 1:1 chats, labelled.
4. No "forward" action, no invite links, no share-to-other-apps for group content. Block screenshots where the phone allows it.
5. Only the people who can use a control should see it: create group (providers), manage members and moderation (`my_role` owner or admin), pin and "tag everyone" (`my_kind` provider), reactions (program groups), typing and "seen by" (care groups), the message box (open group, and providers-only posting respected).
6. A message from before the person joined never appears. Do not try to cache a bigger history.
7. A removed or non-member person gets `403` on everything. Treat it as "leave this group".
8. Do not offer "edit" after the edit window, and do not offer "delete": the actions are **retract** (your own) and **remove** (admin).
9. The person must tick consent on the invitation; never pre-tick it.
10. Emergency wording: do not offer chat as a way to reach urgent help.


## 9. Web: example code

```js
const API = "https://chat.wisdomtooth.tech/api/v1";

async function call(path, options = {}) {
  const res = await fetch(`${API}${path}`, {
    credentials: "include",                    // session cookie; or add an Authorization header
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (res.status === 401) { /* go to sign in */ }
  const data = res.status === 204 ? null : await res.json();
  if (!res.ok) throw Object.assign(new Error(data?.detail || res.statusText), { status: res.status });
  return data;
}

// the Groups tab
const { items: groups } = await call("/groups");

// open a group
const group = await call(`/groups/${id}`);
const page = await call(`/groups/${id}/messages?limit=50`);
await call(`/groups/${id}/read`, { method: "POST" });

// send a reply that tags someone
await call(`/groups/${id}/messages`, {
  method: "POST",
  body: { content: "Please confirm", reply_to_id: quotedId, mentions: [userId] },
});

// older messages
const older = await call(`/groups/${id}/messages?limit=50&cursor=${encodeURIComponent(page.next_cursor)}`);

// parse a server time
const when = new Date(message.created_at + "Z");
```

Handle a new message from the socket without a sender name:
```js
socket.on("new_message", ({ conversation_id, message }) => {
  const sender = membersCache[conversation_id]?.find(m => m.user_id === message.sender_id);
  render({ ...message, sender_name: sender?.name ?? "…" });   // refresh the member list if the sender is unknown
});
```

Notes for web:
- Render message text as text, never as HTML.
- A `403` on `GET /groups/{id}` after a `member_removed` event is expected; close the screen.
- Keep the member list cached per open group and refresh it on membership events (section 6).


## 10. Mobile: notes

- Send the Bearer token on every call. On `401`, refresh the token the way the app already does and retry once.
- **Push.** The payload for a message is `data: { type: "group_message", conversationId, messageId }` with title "New message" and an empty body. On tap, open the group chat. If the app is in the foreground, update the badge and the list instead of showing a banner for the group that is already open.
- **Lock screen.** Keep the title "New message". Do not enrich the notification on the device with names or text.
- **Offline.** Show unsent messages as pending in the app, but do not retry a send automatically after a timeout without first checking the list, because there is no idempotency key.
- **Time.** Read times as UTC. On Android: `LocalDateTime.parse(value).atOffset(ZoneOffset.UTC)`. On iOS: an ISO 8601 formatter with fractional seconds and the time zone set to UTC.
- **Screenshots.** When `screenshots_blocked` is true, set the window flag (Android `FLAG_SECURE`) and hide the content in the app switcher (iOS).
- **Sessions.** Providers use multi-factor sign-in and the app should lock when idle; this is the app's job, not the API's.
- **Deep links.** Route by `conversationId` / `metadata.conversation_id`. If the group is no longer accessible (`403`), show "You are no longer in this group".


## 11. Checklist for a first release

- [ ] Groups tab (patient) and labelled groups in the provider inbox
- [ ] Invitation screen with consent, guardian step for minors, decline
- [ ] Group chat: history with cursor paging, send, reply, tag, files, system lines, placeholders for removed messages
- [ ] Unread count, "new messages" marker, mark read
- [ ] Edit within the window, retract, report
- [ ] Group info: members (names as returned), mute, pin, archive, leave, withdraw consent
- [ ] Provider tools: create, invite, remove, roles, mute someone, hide a message, pin, close and reopen
- [ ] Notices on every screen: "N people can see this" and "Not for emergencies"
- [ ] Realtime events and the removal handling (section 6)
- [ ] Push routing by `data.type == "group_message"`
- [ ] Privacy screens: what I agreed to, data copy, grievance officer
- [ ] Screenshot blocking and no forwarding
