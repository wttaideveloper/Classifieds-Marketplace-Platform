# Group Chat API

For the web and mobile developers, QA, and the clinic's compliance team. All paths start with `https://chat.wisdomtooth.tech/api/v1`. Swagger is at `/docs` (tag "Group Chat").

This replaces the earlier generic design in `group-chat-feature.md` wherever the two differ. That document was written before the healthcare rules (verified providers, consent, care and program groups, audit trail).

Everything here is built and tested, except the items in section 11, which belong to infrastructure, the apps or legal.


## 1. The idea in one page

A group is one conversation with several people, a name, and a group admin. In a healthcare app three rules apply to every group:

1. Only a **verified provider** or a **clinic admin** can create a group. A patient cannot.
2. **Nobody is added without agreeing.** An invited person sees nothing in the group until they accept.
3. Every group is **linked to a program, an appointment series, a care episode or a training**. There are no free-form groups. Training groups have their own document: `group-chat-training.md`.

There are two kinds of group:

| | Care group | Program group |
|---|---|---|
| What it is | one patient's care team | several patients in one program (a class) |
| Members | up to 15 | up to 50 |
| Patients see each other as | full names | display names only |
| Who has read a message | everyone can see | patients cannot, providers can |
| Typing indicator | yes | no |
| Reactions | off | a small set |
| "Providers only" posting | off by default | on by default |
| A new member sees earlier history | only if the patient agreed and the owner shares it | never |
| Appears for a patient in | the Groups tab | the Groups tab |

The sizes, the 15 minute edit window and the three-admin limit are settings (section 10).


## 2. Who can do what

Roles in a group: **owner** (the provider who created it), **admin**, **member**. Each member is also a **provider** or a **patient**. A person counts as a provider only if their registration has been verified (section 9).

| Action | Owner | Admin | Member (provider) | Member (patient) | Compliance |
|---|---|---|---|---|---|
| Create a group | yes (it is the creator) | | verified provider, clinic admin | **no** | |
| Edit name, description, picture | yes | yes | | | |
| Invite people | yes | yes | | | |
| Remove a member | yes | yes (not an admin, not the owner) | | | |
| Remove or demote an admin | yes, with a reason | | | | yes, with a reason |
| Make someone an admin | yes | yes | | | yes |
| Be made an admin | | | verified provider only | **never** | |
| Mute someone from posting | yes | yes (not the owner or another admin) | | | |
| Remove any message ("hide") | yes | yes | | | |
| Pin a message | yes | yes | yes | no | |
| Switch providers-only posting | yes | yes | | | |
| Close the group | yes | yes | | | |
| Reopen the group | yes, with a reason | no | | | yes, with a reason |
| Hand the group to another provider | yes | | | | |
| Reassign the group (owner unreachable) | | | | | yes, with a reason |
| Share earlier history (care group) | yes, with the patient's agreement | | | | |
| Read the audit trail | yes | | | | yes |
| Read the secure record (originals) | yes | | | | yes |
| Legal hold | | | | | yes |
| Verify providers | | | | | yes (own clinic), Super Admin (any) |

Compliance means the **clinic's Enterprise Admin** (for that clinic's groups only) or the **platform's Super Admin** (all groups). Compliance can read a group's details and reports, but not its messages.

Two rules that are easy to miss:
- The owner can never be removed by anyone. To change the owner, the owner hands the group over, or compliance reassigns it.
- The owner cannot leave until the group has been handed to another verified provider.


## 3. The flows

### Creating and joining

1. A verified provider calls `POST /groups` with a name, the kind, the link (program, appointment series, care episode or training) and, optionally, people to invite.
2. Each invited person gets a notification. They are **not members** yet: the group does not appear in their list, and every group endpoint answers 403.
3. The invited person opens `GET /groups/invitations/{id}`. It shows what the group is for, who the providers are, how many people can see it, and what members can see. It shows no messages.
4. They accept with `POST /groups/{id}/accept` and `consent: true`. Someone under 18 must also send a parent's or guardian's name and relationship. The invitation text they saw is saved as their consent record.
5. From that moment they see messages sent **after** they joined. A line "Dr Rao added Priya" appears in the chat.

### Leaving

- Anyone can leave: `POST /groups/{id}/leave`.
- A patient can instead withdraw consent: `POST /groups/{id}/consent/withdraw`. It has the same effect (access ends at once) and the consent record shows the withdrawal time.
- The owner must first hand the group over with `POST /groups/{id}/transfer-ownership`.

### Closing

When the program or care episode ends, an admin calls `POST /groups/{id}/close`. The group becomes read-only and the retention period starts. Reopening needs the owner and a reason.


## 4. API list

### Groups and invitations

| Method | Path | Who | What it does |
|---|---|---|---|
| POST | /groups | verified provider, clinic admin | create a group |
| GET | /groups | any member | my groups (the Groups tab). Filters: `kind`, `archived`, `q`, `page`, `page_size` |
| GET | /groups/{id} | member, or compliance | group details |
| PATCH | /groups/{id} | owner, admin | change name, description, picture |
| PATCH | /groups/{id}/settings | owner, admin | `admins_only_posting` (providers-only posting) |
| PATCH | /groups/{id}/preferences | member | mute or pin the group for me |
| GET | /groups/invitations | anyone | my pending invitations |
| GET | /groups/invitations/{id} | the invited person | one invitation, as shown before agreeing |
| POST | /groups/{id}/accept | the invited person | agree and join |
| POST | /groups/{id}/decline | the invited person | decline |
| POST | /groups/{id}/leave | member | leave |
| POST | /groups/{id}/consent/withdraw | member | take consent back (leaves at once) |
| POST | /groups/{id}/close | owner, admin | close (read-only, retention starts) |
| POST | /groups/{id}/reopen | owner, compliance | reopen, with a reason |

Archiving a group for yourself uses the existing `PATCH /conversations/{id}/archive`.

### Members and roles

| Method | Path | Who | What it does |
|---|---|---|---|
| GET | /groups/{id}/members | member | who is in the group (admins also see pending invitations) |
| POST | /groups/{id}/members | owner, admin | invite more people (named invitation only; there are no invite links) |
| DELETE | /groups/{id}/members/{user_id}?reason= | owner, admin, compliance | remove a member or cancel an invitation |
| PATCH | /groups/{id}/members/{user_id}/role | owner, admin, compliance | `admin` or `member` |
| POST | /groups/{id}/members/{user_id}/mute-posting | owner, admin | can read, cannot post. Optional `until` |
| DELETE | /groups/{id}/members/{user_id}/mute-posting | owner, admin | unmute |
| POST | /groups/{id}/transfer-ownership | owner | hand the group to another verified provider |
| POST | /groups/{id}/reassign-owner | compliance | name a new owner, with a reason |
| POST | /groups/{id}/members/{user_id}/share-history | owner | care group: let a provider see earlier messages |
| POST / DELETE | /groups/{id}/history-sharing/consent | the patient | agree, or stop agreeing, that earlier history may be shared |

### Messages

| Method | Path | Who | What it does |
|---|---|---|---|
| GET | /groups/{id}/messages | member | read messages. `cursor`, `limit` |
| POST | /groups/{id}/messages | member | send. Supports `reply_to_id`, `mentions`, `mention_all` |
| POST | /groups/{id}/read | member | mark the group read |
| PATCH | /groups/{id}/messages/{mid} | the author | edit, within 15 minutes |
| POST | /groups/{id}/messages/{mid}/retract | the author | take my message out of the group |
| POST | /groups/{id}/messages/{mid}/hide | owner, admin | remove any message, with a reason |
| PUT / DELETE | /groups/{id}/messages/{mid}/pin | provider | pin or unpin |
| GET | /groups/{id}/pinned | member | pinned messages |
| PUT | /groups/{id}/messages/{mid}/reactions | member | react (program groups) |
| DELETE | /groups/{id}/messages/{mid}/reactions?emoji= | member | take my reaction back |
| GET | /groups/{id}/reactions | member | which reactions are allowed |
| GET | /groups/{id}/messages/{mid}/read-status | member | "seen by 4 of 6" |
| GET | /groups/{id}/search?q= | member | search, only what was sent after joining |
| GET | /groups/{id}/files | member | shared files, only after joining |
| POST | /groups/{id}/messages/{mid}/save-to-record | provider | save a message to a patient's clinical record |
| GET | /groups/{id}/clinical-records | provider, compliance | messages saved to records |
| GET | /groups/{id}/messages/{mid}/original | owner, compliance | the secure record: original text and versions |

Files and photos are uploaded with the existing attachment API, then sent by `attachment_id` in the message.

The old generic endpoints (`GET /conversations/{id}/messages`, `PATCH` and `DELETE /messages/{id}`, the message read-status and message search) refuse a group with `409 This is a group chat. Use the /api/v1/groups endpoints`. The socket `send_message` event and `POST /messages` still work for a group, with all the group rules applied.

### Compliance, privacy and verification

| Method | Path | Who | What it does |
|---|---|---|---|
| GET | /groups/{id}/audit | owner, compliance | audit trail (read only) |
| POST | /groups/{id}/reports | member | report a message or the group |
| GET | /groups/reports | compliance | reports (clinic: its own, Super Admin: all). Filters: `status`, `group_id` |
| PATCH | /groups/reports/{id} | compliance | `reviewing`, `resolved`, `dismissed`, with a note |
| POST / DELETE | /groups/{id}/legal-hold | compliance | put on, or lift, a legal hold |
| GET | /groups/compliance/groups | compliance | all groups of the clinic (or all, for the Super Admin). Details only, no messages |
| GET | /groups/consents/me | anyone | everything I agreed to, and when I withdrew |
| POST | /groups/data-requests | anyone | ask for a copy of my group chat data |
| GET | /groups/data-requests/me | anyone | my requests |
| GET | /groups/grievance-officer | anyone | who to contact with a complaint |
| POST | /groups/verified-providers | clinic admin, Super Admin | record that a provider's registration was verified |
| GET | /groups/verified-providers | clinic admin, Super Admin | list |
| DELETE | /groups/verified-providers/{user_id} | clinic admin, Super Admin | revoke |


## 5. Samples

These are real responses from the tests.

### Create a group

`POST /groups`
```json
{
  "name": "Yoga batch Oct 2026",
  "kind": "program",
  "description": "Weekly yoga for beginners",
  "context_type": "program",
  "context_id": "e7ef5b55-c305-4dea-b4fe-47119768f2b1",
  "invitees": [
    { "user_id": "c34d2811-7602-4561-ad95-742a7e4e84a6" },
    { "user_id": "d30e894d-15fd-4b37-86b4-c128012dbb44", "is_minor": true }
  ]
}
```
`context_type` is `program`, `appointment_series` or `care_episode`. The name must not contain a patient's name or a health condition (422). For a program the server checks the program exists.

Response 201 (the same shape comes back from `GET /groups/{id}`):
```json
{
  "id": "6918ddf8-e923-4bd1-8067-84c444842799",
  "name": "Yoga batch Oct 2026",
  "kind": "program",
  "description": "Weekly yoga for beginners",
  "avatar_url": null,
  "context_type": "program",
  "context_id": "e7ef5b55-c305-4dea-b4fe-47119768f2b1",
  "tenant_id": "f1c33ada-26de-4f7c-beeb-3336dc458622",
  "status": "open",
  "admins_only_posting": true,
  "created_by": "6173a3a3-7248-4643-a533-4607c43e9d0b",
  "created_at": "2026-10-12T10:41:29.770727",
  "member_count": 1,
  "visibility_notice": "1 person can see this",
  "emergency_notice": { "text": "Not for emergencies. In an emergency call 112.", "number": "112" },
  "forwarding_blocked": true,
  "screenshots_blocked": true,
  "limits": { "max_members": 50, "max_admins": 3, "edit_window_minutes": 15 },
  "my_role": "owner",
  "my_kind": "provider",
  "muted": false,
  "pinned": false,
  "is_archived": false,
  "unread_count": 0,
  "last_message_at": "2026-10-12T10:41:29.786900",
  "last_message_preview": "Dr Rao created the group",
  "closed_at": null,
  "closed_reason": null,
  "retention_started_at": null,
  "retention_until": null,
  "legal_hold": false
}
```
`member_count` counts the people who can see the group (active members). It does not count pending invitations. Show `visibility_notice` and `emergency_notice` on every group screen.

### The invitation

`GET /groups/invitations/{id}`
```json
{
  "conversation_id": "6918ddf8-e923-4bd1-8067-84c444842799",
  "name": "Yoga batch Oct 2026",
  "kind": "program",
  "description": "Weekly yoga for beginners",
  "purpose": "This is a program group for several patients taking part in one program.",
  "invited_by_name": "Dr Rao",
  "provider_names": ["Dr Rao"],
  "member_count": 1,
  "visibility_notice": "1 person can see this",
  "what_members_can_see": "Everyone in the group can read every message and file posted after they join. Patients see each other by display name only; no phone numbers or profile details are shared. You can leave at any time.",
  "emergency_notice": { "text": "Not for emergencies. In an emergency call 112.", "number": "112" },
  "requires_guardian_consent": false,
  "invited_at": "2026-10-12T10:41:29.771712"
}
```
Show all of it before the person taps accept. When `requires_guardian_consent` is true, the app must collect the parent's or guardian's name and relationship.

### Accept

`POST /groups/{id}/accept`
```json
{ "consent": true, "display_name": "Priya S.", "guardian": { "name": "Meena Kumar", "relationship": "mother" } }
```
`guardian` is only needed when the invitation says so. `display_name` is what other patients see in a program group. If it is left out, the server uses first name and the initial of the last name ("Priya S."). An email address is never accepted as a display name. The response is the group (above).

### Members, as a patient sees them in a program group

`GET /groups/{id}/members`
```json
{
  "members": [
    { "user_id": "6173a3a3-…", "name": "Dr Rao", "role": "owner", "kind": "provider", "status": "active", "is_me": false, "joined_at": "2026-10-12T10:41:29.767587", "posting_muted": false, "posting_muted_until": null },
    { "user_id": "c34d2811-…", "name": "Priya Sharma", "role": "member", "kind": "patient", "status": "active", "is_me": true, "joined_at": "2026-10-12T10:41:29.846219", "posting_muted": false, "posting_muted_until": null },
    { "user_id": "d30e894d-…", "name": "Ravi K.", "role": "member", "kind": "patient", "status": "active", "is_me": false, "joined_at": "2026-10-12T10:41:29.874831", "posting_muted": false, "posting_muted_until": null }
  ],
  "total": 3
}
```
Providers are shown in full. Another patient is shown by display name only. In a care group everyone is shown in full. There is never a phone number, email or profile detail. `posting_muted` is only filled in for admins. Admins also see people with `status: "invited"`.

### Send a message, with a reply

`POST /groups/{id}/messages`
```json
{ "content": "Thanks, see you then", "reply_to_id": "deb9dd74-ae28-45ba-b2c7-9f8775fde045" }
```
For an @-tag send `"mentions": ["<user id>"]`. Providers can send `"mention_all": true`. For a file, upload it first and send `{ "message_type": "image", "attachment_id": "…" }`.

Response 201:
```json
{
  "id": "0b92cf4d-d944-4b15-82a5-e19efcc76de0",
  "conversation_id": "6918ddf8-e923-4bd1-8067-84c444842799",
  "sender_id": "c34d2811-7602-4561-ad95-742a7e4e84a6",
  "sender_name": "Priya Sharma",
  "sender_kind": "patient",
  "message_type": "text",
  "content": "Thanks, see you then",
  "attachment_id": null,
  "status": "visible",
  "placeholder": null,
  "reply_to": { "id": "deb9dd74-…", "sender_name": "Dr Rao", "preview": "Class moves to 7am on Friday", "is_hidden": false },
  "mentions": [],
  "mentions_all": false,
  "mentions_me": false,
  "is_edited": false,
  "edited_at": null,
  "pinned": false,
  "reactions": {},
  "my_reactions": [],
  "read_by": null,
  "created_at": "2026-10-12T10:41:30.008326"
}
```
Names in a message are chosen for the person reading it, so the same message can show a different `sender_name` to a provider and to another patient.

### Read messages

`GET /groups/{id}/messages?limit=50` returns
```json
{
  "items": [ { "…": "messages as above, oldest first" } ],
  "has_more": false,
  "next_cursor": null,
  "unread_count": 2,
  "first_unread_message_id": "…",
  "pinned_count": 0
}
```
- To load older messages, pass `next_cursor` as `cursor`.
- Put the "new messages" marker above `first_unread_message_id`, then call `POST /groups/{id}/read`.
- A message taken out of the group is returned with no text:
```json
{ "id": "500ad83d-…", "sender_name": "Ravi K.", "message_type": "text", "content": null, "attachment_id": null,
  "status": "removed_by_admin", "placeholder": "Message removed by admin", "…": "…" }
```
`status` is `visible`, `retracted` (the author took it back, placeholder "Message retracted") or `removed_by_admin`. Show the `placeholder` text in its place.
- `read_by` is a list of user ids in a care group and `null` in a program group.
- System lines ("Dr Rao added Priya", "Dr Mehta left") have `message_type: "system"`.

### Who has seen a message

`GET /groups/{id}/messages/{mid}/read-status`
```json
{ "message_id": "deb9dd74-…", "seen_by_count": 0, "recipient_count": 2, "label": "seen by 0 of 2", "seen_by": [] }
```
In a care group everyone can call it. In a program group a patient gets 403 and a provider gets the answer. `recipient_count` counts the people who were in the group when the message was sent.

### Edit, retract, remove

- `PATCH /groups/{id}/messages/{mid}` `{ "content": "…" }` works for the author within 15 minutes, only for text. The message then shows `is_edited: true`. Every earlier version is kept.
- `POST …/retract` takes the author's own message out of the group, at any time.
- `POST …/hide` `{ "reason": "shared a phone number" }` is for an owner or admin.
- Nothing is deleted. The owner and compliance can read the original: `GET …/original`
```json
{
  "message_id": "500ad83d-…",
  "sender_id": "d30e894d-…",
  "status": "removed_by_admin",
  "content": "oops wrong chat",
  "versions": [],
  "current_content": "oops wrong chat",
  "hidden_by": "6173a3a3-…",
  "hidden_reason": "shared a phone number",
  "hidden_at": "2026-10-12T10:41:30.067753",
  "created_at": "2026-10-12T10:41:30.036883"
}
```
`content` is the text as first written, `versions` are the earlier versions (oldest first) and `current_content` is the text now. Every read is written to the audit trail.

### Audit trail

`GET /groups/{id}/audit?action=&target_user_id=&page=&page_size=`
```json
{ "items": [
  { "id": "73be3e7c-…", "conversation_id": "6918ddf8-…", "actor_id": "6173a3a3-…", "actor_role": "provider",
    "action": "message_hidden", "target_user_id": "d30e894d-…", "message_id": "500ad83d-…",
    "reason": "shared a phone number", "details": null, "created_at": "2026-10-12T10:41:30.070032" },
  { "action": "settings_changed", "details": { "admins_only_posting": { "from": true, "to": false } }, "…": "…" }
], "total": 12 }
```
It never contains message text. Actions logged: `group_created`, `group_updated`, `member_invited`, `invitation_cancelled`, `consent_granted`, `consent_declined`, `consent_withdrawn`, `member_removed`, `member_left`, `role_changed`, `ownership_transferred`, `owner_reassigned`, `posting_muted`, `posting_unmuted`, `settings_changed`, `group_closed`, `group_reopened`, `message_edited`, `message_retracted`, `message_hidden`, `message_pinned`, `message_unpinned`, `history_sharing_agreed`, `history_sharing_withdrawn`, `history_shared`, `report_filed`, `report_resolved`, `report_dismissed`, `report_reviewing`, `legal_hold_set`, `legal_hold_cleared`, `clinical_record_saved`, `original_viewed`. There is no route that changes or deletes an entry.

### Report

`POST /groups/{id}/reports` `{ "message_id": "…", "reason": "privacy", "details": "…" }`. `message_id` is optional (leave it out to report the group). `reason` is one of `harassment`, `privacy`, `health_information`, `spam`, `inappropriate`, `safety`, `other`. The response is the report with `status: "open"`; compliance and the platform team are notified.

### What I agreed to

`GET /groups/consents/me` returns one item per group the person agreed to join, with `granted_at`, `withdrawn_at`, the guardian details if there were any, and `notice`, which is the exact invitation text they saw.


## 6. Realtime events (Socket.IO)

Connect and authenticate as before. The server puts a person's connected devices into the group's room when they accept, and takes them out of the room when they are removed or leave, so a removed person stops receiving messages straight away even with the app open. (This works while the server runs as one process, which is how it runs today. See section 11 for a split deployment.)

To the group's room (`conversation:<id>`), every payload also has `conversation_id`:

| Event | When | Payload |
|---|---|---|
| `new_message` | a message was sent | `message` (the same for every reader: ids, text, `reply_to_id`, `mentions`, no names) |
| `message_updated` | a message was edited | `message` |
| `message_hidden` | retracted or removed by an admin | `message_id`, `status`, `placeholder` |
| `message_pinned` | pinned or unpinned | `message_id`, `pinned` |
| `reaction_updated` | a reaction was added or removed | `message_id`, `reactions` |
| `member_joined` | someone accepted | `user_id`, `role`, `kind` |
| `member_left` | someone left | `user_id` |
| `member_removed` | someone was removed | `user_id` |
| `member_role_changed` | admin or owner changed | `user_id`, `role` |
| `group_updated` | name, description, picture or the posting setting changed | the changed fields |
| `group_closed`, `group_reopened` | closed or reopened | |
| `typing` | care groups only | `user_id`, `is_typing` |

To one person's own room (`user:<id>`):

| Event | When |
|---|---|
| `group_invitation` | they were invited (`conversation_id`) |
| `member_removed` | they were removed (use it to close the open screen and drop the group from the list) |
| `posting_muted`, `posting_unmuted` | an admin muted or unmuted them (`until` when timed) |

Messages carry no sender names because each person may see a different name for the same sender. The app takes names from `GET /groups/{id}/members`, which it should refresh on `member_joined`, `member_left`, `member_removed` and `member_role_changed`.


## 7. Notifications

They use the existing notification feed (`GET /users/me/notifications`) and the Socket.IO `notification` event. `metadata.category` and `metadata.conversation_id` are always set, so the app can open the right group.

| Category | Sent to | When |
|---|---|---|
| group_invitation | the invited person | someone invited them |
| group_message | every active member except the sender, and except those who muted the group | a message was sent |
| group_removed | the person removed | an admin removed them |
| group_role_changed | the person | they became an admin, a member or the owner (`metadata.role`) |
| group_closed, group_reopened | all active members | the group was closed or reopened |
| group_report_filed | the clinic's Enterprise Admins and the Super Admins | a report was filed (`metadata.report_id`) |

Privacy rules that the app can rely on:

- A message notification says only **"New message"**. It has no group name, no sender and no text. The same is true of push, the in-app notification and the realtime event. Email and SMS are not used for group messages at all.
- An invitation notification does not contain the group name either.
- An @-tag notifies the tagged person with the same "New message" text (`mentioned: true` in the chat notification data). In a **care group** it reaches them even if they muted the group. In a program group a mute is always respected.
- Muting a group (`PATCH /groups/{id}/preferences`, with an optional `muted_until`) never removes access.


## 8. Things the app must do

The server enforces the rules above, but a few things are the app's job:

1. Show `visibility_notice` ("N people can see this") and `emergency_notice` ("Not for emergencies") on every group screen and on the invitation.
2. Patients see groups in a separate **Groups** tab (`GET /groups`). `GET /conversations` leaves groups out for patients. Providers get groups in the same inbox as 1:1 chats, marked `conversation_type: "group"` with `group_kind`; the group name is in `subject`.
3. Block forwarding out of the app. There is no forward endpoint. Block screenshots where the phone allows it (`screenshots_blocked` is the hint).
4. Do not show a patient's real name in a program group. Use the `name` the API returns.
5. Hide the controls the person cannot use: create (patients), admin actions (members), pin (patients), reactions (care groups), typing (program groups), read-by (program groups, patients).
6. Treat 403 on any group endpoint as "you are not in this group" and remove it from the list.


## 9. Verified providers

A provider can create a group, own one or be made an admin only if their registration has been verified. A clinic admin (for their own clinic) or the Super Admin records it:

`POST /groups/verified-providers`
```json
{ "user_id": "…", "registration_number": "MH-12345", "registration_council": "State Medical Council" }
```

Before the first group can be created, every provider who needs to must be added this way. The registration number and council are required, and the server confirms that the target is an active Provider in that clinic using the Identity tenant-members source. This is a one-time load: use `GET /groups/verified-providers` to check current (not revoked) entries. A provider whose verification is revoked can no longer create groups; groups they already own are left alone (the owner can hand them over).

An Enterprise Admin's clinic is taken from their authenticated tenant identity; they cannot supply another clinic. A Platform Super Admin can verify for any clinic, but must include that clinic's `tenant_id` in the request. A verification only authorizes groups in the recorded clinic.

A clinic admin can create groups without being in this list. If a clinic admin should also be made an admin of someone else's group, add them to the list.

A provider who is not verified, when invited into a group, is treated like a patient there (they cannot pin, tag everyone or be made an admin).


## 10. Settings

Set in the server environment. The values below are the defaults.

| Setting | Default | Meaning |
|---|---|---|
| GROUP_MAX_MEMBERS_CARE | 15 | members in a care group (to confirm) |
| GROUP_MAX_MEMBERS_PROGRAM | 50 | members in a program group (to confirm) |
| GROUP_MAX_ADMINS | 3 | admins besides the owner |
| GROUP_EDIT_WINDOW_MINUTES | 15 | how long an author can edit (to confirm) |
| GROUP_REQUIRE_VERIFIED_PROVIDER | true | providers need a verification to create a group |
| GROUP_PROGRAM_PATIENTS_CAN_POST | true | false = patients can never post in a program group |
| GROUP_PROGRAM_REACTIONS | 👍 ❤️ 🙏 👏 😊 | reactions allowed in a program group |
| GROUP_NAME_BLOCKED_TERMS | empty | comma-separated health conditions that may not appear in a name (whole words) |
| GROUP_EMERGENCY_NUMBER | 112 | shown in the emergency notice |
| GROUP_RETENTION_DAYS | not set | how long the record is kept after a group closes (set with legal advice) |
| GROUP_GRIEVANCE_OFFICER_NAME / _EMAIL / _PHONE | empty | returned by `GET /groups/grievance-officer` |

Pending invitations count against the member limit.


## 11. What this backend does not do

These come from the same specification but are outside what an API can do, or wait for a decision:

| Item | Where it belongs |
|---|---|
| Messages and files encrypted | database and storage encryption (infrastructure) |
| Providers sign in with multi-factor authentication; sessions lock when idle | the login service and the apps |
| Files limited to approved types and scanned for malware | the upload endpoint takes the type limits it always had; there is no malware scanner yet |
| Blocking screenshots and forwarding | the apps. The API has no forward endpoint and returns `screenshots_blocked` as a hint |
| Saved messages reaching the real clinical record | the API stores a snapshot (`GET /groups/{id}/clinical-records`). Sending it to a health-record system is an integration, if one is planned |
| Deleting data when the retention period ends | nothing is ever deleted by the API. `retention_until` is the date a clean-up job can use, and `legal_hold` stops it |
| "Read-only until reassigned" when an owner cannot be reached | compliance can close the group, reassign it, and reopen it |
| Socket rooms in a split (multi-server) deployment | rooms are managed on the server that holds the connection. With Redis and several servers, a removed person on another server stays in the room until they disconnect. Their sends and reads are still refused |
| Name checks for a health condition | the name is checked against the people in the group and the configured blocked words, not by understanding the text |
| Appointment series and care episodes existing | a program link is checked against the programs table. An appointment series or care episode has no table in this service yet, so only its type is checked |

To confirm with legal and compliance (from the specification): retention periods by group type and for minors; the group sizes and the edit window; which data-protection rules and breach timelines apply; whether HIPAA applies; whether patients may post to each other in program groups at all (`GROUP_PROGRAM_PATIENTS_CAN_POST`).


## 12. Errors

| Status | Meaning here |
|---|---|
| 401 | not logged in |
| 403 | not allowed: not a member, not an admin, a patient trying a provider action, muted, providers-only posting. The `detail` says which |
| 404 | the group, message, member or invitation does not exist, or the message is from before the person joined |
| 409 | the state does not allow it: group closed, group full, owner must hand over first, already closed |
| 422 | the input is wrong: name with a patient's name or a condition, missing link, consent not true, guardian missing for a minor, a reason is required, unknown emoji |

Errors look like `{"detail": "message"}`.


## 13. Deploy

1. Run the repository's full migration deployment command: `alembic upgrade heads`. The group-chat migration is `e5b2c7a9d1f3` (after `b6d1f3a8c2e4` and `d3a8f1c6e9b2`); `b3e7c1a9d5f4` follows it. The group migration adds the group columns and new tables without changing existing chats (every current participant becomes `active`).
2. Add the verified providers (section 9) and set the settings you need (section 10).
3. Set `GROUP_GRIEVANCE_OFFICER_*` and the retention period once legal has confirmed them.
