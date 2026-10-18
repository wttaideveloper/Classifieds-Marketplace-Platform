# Group Chat for Trainings: Backend API

For the web developers. It follows the proposed web flow (Enterprise Admin and Provider, Trainings only) and answers the backend questions in that flow. The general group chat rules, messages, sockets and compliance calls are in `group-chat-api.md`; this document only adds what is new for Trainings.

All paths start with `https://chat.wisdomtooth.tech/api/v1/groups`. Send the token as `Authorization: Bearer <token>`. Swagger is at `/docs` (tag "Group Chat").

The one to one chat (`/conversations`, `/messages`) is not touched.


## 1. How it works

1. The Enterprise Admin or a verified Provider picks a Training (`GET /groups/trainings`).
2. They see the enrolled learners of that Training, searchable and paged (`GET /groups/trainings/{id}/learners`). Each learner comes with the `user_id` to invite.
3. They can add verified Providers (`GET /groups/trainings/{id}/providers`).
4. They create the group (`POST /groups`) with `context_type: "training"`. The creator becomes the Group Owner. Everyone invited gets an invitation and becomes a member only after accepting.
5. Later the owner or an admin can invite more people, or everyone who enrolled afterwards (`POST /groups/{id}/members/invite-enrolled`).
6. One Training can have many groups. A Training with more learners than the group limit needs more than one group.

Words used by the API: a learner is `kind: "patient"` in member lists (the general group chat calls every non-provider a patient). Show it as "Learner". A group admin or owner is always a verified Provider (or the Enterprise Admin who created the group).


## 2. Who can do what

| | Enterprise Admin (own clinic) | Verified Provider | Learner | Super Admin |
|---|---|---|---|---|
| See the trainings, learners and Providers lists | all trainings of the clinic | only trainings assigned to them (see 2.1) | no (403) | no (403) |
| Create a training group | yes | yes, for those trainings | no | no |
| Become the Group Owner when they create it | yes | yes | | |
| Read the messages of a group they are not in | **no** | no | | no |
| See a group's details (not messages) | yes, for every group of the clinic | only their own | | |

A user of another clinic always gets 403 on a training that is not theirs.

### 2.1 Which trainings a Provider may use

A Provider can use a Training when they are its assigned instructor: `Training.instructor_id` is their user id. The instructor name typed in the Training form is only text. It does not give anyone access and nobody is added to a group because of it.

If your Training form does not save `instructor_id`, Providers will see an empty list. The backend has a setting for that case: `GROUP_PROVIDER_REQUIRES_TRAINING_ASSIGNMENT`. When it is `false`, every verified Provider of the clinic can use every Training of the clinic. The default is `true` (assigned only). Enterprise Admins are not affected by the setting.


## 3. APIs

### 3.1 GET /groups/trainings: trainings I can start a group for

Query (all optional): `q` (words in the title), `status` (the training status), `page` (default 1), `page_size` (default 20, maximum 100).

```json
{
  "items": [
    {
      "id": "7b0c…", "title": "Yoga Basics", "status": "published", "delivery_mode": "online",
      "instructor_name": "Dr Rao",
      "instructor_user_id": "d9d8…",
      "instructor_is_verified_provider": true,
      "enrolled_count": 60,
      "group_count": 2,
      "created_at": "2026-09-01T10:00:00"
    }
  ],
  "total": 1, "page": 1, "page_size": 20
}
```
- `enrolled_count` is the number of learners who can be invited (status enrolled, active, completed, approved or attended). Pending, rejected, cancelled and waitlisted learners are not counted.
- `instructor_name` is the text from the Training form. Show it as information. `instructor_user_id` is only filled when the Training has an `instructor_id`.
- 403 for a learner, a Provider whose registration is not verified, or a Super Admin.

### 3.2 GET /groups/trainings/{training_id}/learners: the enrolled learners

Query (all optional):

| Name | Meaning |
|---|---|
| q | words in the learner's name or email |
| eligible_only | default `true`: only learners who can be invited. Send `false` to see everyone |
| enrolment_status | show one enrolment status, for example `cancelled` |
| not_in_group | `true` hides everyone who is already a member of, or invited to, a group of this Training. Use it to find people who enrolled later |
| group_id | with `not_in_group` or for `group_state`: look at one group of this Training only |
| page, page_size | default 1 and 50, page size maximum 200 |

```json
{
  "training_id": "7b0c…",
  "training_title": "Yoga Basics",
  "items": [
    {
      "enrolment_id": "1d2e…",
      "name": "Priya Sharma",
      "email": "priya.sharma@example.com",
      "enrolment_status": "enrolled",
      "user_id": "a4f1…",
      "can_invite": true,
      "reason": null,
      "group_state": "none",
      "group_ids": [],
      "enrolled_at": "2026-09-03T08:30:00"
    },
    {
      "enrolment_id": "9c8b…", "name": "Walk In", "email": "walkin@example.com", "enrolment_status": "enrolled",
      "user_id": null, "can_invite": false, "reason": "no_account", "group_state": "none", "group_ids": [], "enrolled_at": "…"
    }
  ],
  "total": 60, "page": 1, "page_size": 50,
  "eligible_total": 60,
  "max_members": 50
}
```
- Sorted by name. `total` is after the filters; `eligible_total` is not.
- `user_id` is the id to send when inviting. It is `null` when no chat account could be found for the learner. Then `can_invite` is `false` and `reason` is `no_account`. Show the row greyed out with "No account yet". The learner gets an id once their account is in the clinic's member list or after they open their own trainings list (`GET /trainings/my/enrolments`) while signed in; nothing is lost.
- `reason` is one of `not_eligible_status`, `no_account`, `already_member`, `already_invited`, or `null`.
- `group_state` is `none`, `invited`, `member`, `declined`, `left` or `removed`, for the groups of this Training (or for `group_id`). A learner who declined, left or was removed can be invited again (`can_invite` stays `true`).
- `email` is shown because the clinic's staff see it in the enrolment list already. Do not show it to other learners.
- `max_members` is the group limit (owner and pending invitations count).

### 3.3 GET /groups/trainings/{training_id}/providers: Providers I can add

Query: `q` (name or registration number).
```json
{
  "training_id": "7b0c…",
  "instructor_name": "Dr Rao",
  "items": [
    { "user_id": "d9d8…", "name": "Dr Rao", "registration_number": "REG-ao", "is_training_instructor": true, "is_me": true },
    { "user_id": "55aa…", "name": "Dr Mehta", "registration_number": "REG-hta", "is_training_instructor": false, "is_me": false }
  ],
  "total": 2
}
```
- Only verified Providers of the clinic. The Provider assigned to the Training (`instructor_id`) is marked and listed first. Nobody is added automatically.
- If the Training has only a typed instructor name, no row is marked. The user picks the Provider from the list.
- A normal staff member who is not verified is not listed and cannot be invited as a Provider. The Enterprise Admin verifies someone with `POST /groups/verified-providers` (see `group-chat-api.md`, section 9).

### 3.4 POST /groups: create the group

```json
{
  "name": "October Yoga Batch",
  "kind": "program",
  "description": "Class updates and questions",
  "avatar_url": "https://…/icon.png",
  "context_type": "training",
  "context_id": "7b0c…",
  "invitees": [
    { "user_id": "a4f1…" },
    { "user_id": "55aa…" }
  ],
  "admins_only_posting": true
}
```
- `kind` must be `program` for a training group, otherwise 422.
- `invitees` can mix learners and verified Providers. Each one must be an enrolled learner of this Training or a verified Provider of the clinic. If not, 422:
  ```json
  { "detail": { "message": "These people are not enrolled in this training and are not verified providers of the clinic, so they cannot be invited.", "invalid_user_ids": ["…"] } }
  ```
  Nothing is created in that case.
- To invite everyone eligible without sending the ids, add `"invite_all_enrolled": true` (and `"exclude_user_ids": [...]` to leave some out). The backend invites eligible learners with an account, oldest enrolment first, until the group is full. The people in `invitees` come first. Use `GET /groups/{id}/members` to see who was invited.
- The creator becomes the Group Owner, whether they are an Enterprise Admin or a Provider. An Enterprise Admin can write in the group with their normal login.
- `admins_only_posting`: `true` means only providers can write (announcement style). The default for a training group is `true`. This is the "who can send messages" field. It can be changed later with `PATCH /groups/{id}/settings`.
- Errors: 403 (not allowed to use this Training, see section 2), 422 (training not found, wrong kind, invitee not eligible, group name contains a person's name or a blocked word), 409 (more people than the group limit).
- The answer is the group, like `GET /groups/{id}`, with `context_type: "training"`, `context_id` and `context_name` (the Training title).

### 3.5 POST /groups/{group_id}/members/invite-enrolled: invite more people later

Owner or admin of a training group only.

Invite chosen people:
```json
{ "user_ids": ["a4f1…", "55aa…"] }
```
Or everyone eligible who is not in the group yet:
```json
{ "all_eligible": true, "exclude_user_ids": ["a4f1…"] }
```
Send one of the two, not both (422).
```json
{
  "invited": ["…", "…"],
  "skipped": ["…"],
  "no_account": 1,
  "not_invited_group_full": 0,
  "free_places": 31
}
```
- `skipped` are people already active or already invited.
- `not_invited_group_full` is the number of eligible learners left out because the group is full. Show "12 more learners could not fit. Create another group for them".
- `no_account` is the number of eligible enrolments with no chat account.
- With `user_ids`, the same eligibility check as in 3.4 applies (422 with `invalid_user_ids`) and going over the limit gives 409.
- The plain `POST /groups/{id}/members` also checks eligibility for training groups.
- Not a training group: 422.

### 3.6 GET /groups/trainings/{training_id}/groups: groups already made for a Training

Returns `{ "items": [group, ...], "total": n }` with the same group object as `GET /groups`. A Provider sees the groups they are in. The Enterprise Admin sees all groups of the Training in their clinic, as details only (members and messages stay private to the members).

### 3.7 Other calls you will use (unchanged, see group-chat-api.md)

| What | Call |
|---|---|
| My groups (the Groups tab). Training groups have `context_type: "training"` and `context_name` | GET /groups |
| Group details, notices | GET /groups/{id} |
| Members with role and status (owner/admin also see pending invitations) | GET /groups/{id}/members |
| Make a member an admin, or an admin a member | PATCH /groups/{id}/members/{user_id}/role |
| Remove a member or cancel an invitation | DELETE /groups/{id}/members/{user_id} |
| Hand the group to another verified Provider | POST /groups/{id}/transfer-ownership |
| Compliance names a new owner | POST /groups/{id}/reassign-owner |
| Edit name, description, picture | PATCH /groups/{id} |
| Who can send messages | PATCH /groups/{id}/settings |
| Close, reopen | POST /groups/{id}/close, POST /groups/{id}/reopen |
| Leave | POST /groups/{id}/leave |
| My invitations; accept; decline | GET /groups/invitations, POST /groups/{id}/accept, POST /groups/{id}/decline |
| Messages | GET and POST /groups/{id}/messages |
| Compliance list of groups | GET /groups/compliance/groups |


## 4. Owner and admin management

Matches section 3.3 of the proposed flow.

| Action | Call | Rule |
|---|---|---|
| Make Group Admin | `PATCH /groups/{id}/members/{user_id}/role` with `{"role": "admin"}` | the member must have joined and be a verified Provider (a learner gets 422). At most 3 admins besides the owner (409) |
| Remove Admin Role | the same call with `{"role": "member", "reason": "…"}` | only the owner (or compliance). A reason is required (422 without it) |
| Transfer ownership | `POST /groups/{id}/transfer-ownership` with `{"user_id": "…"}` | owner only; the new owner must be an active member and a verified Provider. The old owner becomes an admin (or a member if there are already 3 admins) |
| Owner leaves | `POST /groups/{id}/leave` | 409 for the owner: transfer first, or close the group |
| Owner is gone or inactive | `POST /groups/{id}/reassign-owner` with `{"user_id": "…", "reason": "…"}` | the clinic's Enterprise Admin or the platform, with a reason |

A confirmation popup in the web app is up to you; the backend does not need one.

What a Group Admin can do: see members and pending invitations, invite eligible learners and verified Providers, remove regular members, edit name, description and picture, change who can send messages, close the group, mute a member from posting, remove (hide) a message, pin messages, and write in the group. A Group Admin cannot remove the owner, cannot remove another admin (only the owner or compliance can), and cannot become the owner by themselves.

Every one of these actions is written to the group's audit trail.


## 5. Answers to the backend questions

1. **Can the group link to a Training?** Yes. `context_type: "training"` and `context_id` is the Training id. The backend checks that it exists, belongs to the caller's clinic and that the caller may use it.
2. **Does every enrolment have a user_id?** No. It is empty for enrolments added by an admin, by a group or at checkout. The roster fills it in when it can (from another enrolment with the same email, or from the clinic's member list) and saves it on the enrolment. If no account is found the row has `user_id: null` and `can_invite: false`. People added with only a name and email are therefore listed but cannot be invited until they have an account.
3. **How is the instructor linked to a Provider?** Through `Training.instructor_id`, not through the typed name. The Providers list marks that Provider. The user still chooses the Provider; nobody is added from the text. If the Training form does not save `instructor_id`, the link does not exist for that Training (see 2.1).
4. **A searchable, paged learner list?** Yes, 3.2.
5. **Does the backend check the invited learner belongs to the Training and the Provider has access?** Yes on both. Creating the group and inviting later check every invitee (enrolled in this Training, or a verified Provider of the clinic). The Provider's access to the Training is checked on every call (assigned, same clinic, verified).
6. **Several groups for one Training? What about people who enroll later?** Yes, any number. Later enrolments are not added automatically (consent is needed anyway). They show up in the roster with `group_state: "none"`; `not_in_group=true` lists only them; `invite-enrolled` with `all_eligible` invites them. If the group is full, create another group.
7. **Can the Enterprise Admin invite normal internal staff?** Only verified Providers (and enrolled learners of the Training). Staff who are not verified can be verified first by the Enterprise Admin, then they appear in the Providers list.
8. **Can the Enterprise Admin create and chat with their existing login?** Yes. They become the owner and can read and write in the group.
9. **Enterprise Admin creates a group: is the admin the owner?** Yes.
10. **Provider creates a group: is the Provider the owner?** Yes.
11. **Can the owner promote a verified Provider member to admin?** Yes (section 4).
12. **Can the owner remove admin permissions?** Yes, with a reason.
13. **Several admins?** Yes, up to 3 besides the owner (setting `GROUP_MAX_ADMINS`).
14. **What can a Group Admin do?** See the list in section 4.
15. **Can the owner transfer ownership?** Yes, to an active member who is a verified Provider.
16. **What if the owner leaves or becomes inactive?** The owner cannot leave without transferring (409). If the owner is gone, the Enterprise Admin or the platform reassigns the group with a reason.
17. **If a Provider creates a group, can the Enterprise Admin see only the group information?** Yes. They can open the details (`GET /groups/{id}`, the Training's group list, the compliance list), the audit trail and reports. They cannot read the messages or the member list, they are not a member, and they cannot write. They can be added as a member like anyone else (if they are a verified Provider), and then they see what members see.
18. **Are the APIs for promote, remove admin and transfer ownership available?** Yes, they are built and tested (section 4).


## 6. Rules the web app should respect

- A training group has up to 50 members, counting the owner, joined members and pending invitations (`max_members` in the learners answer; setting `GROUP_MAX_MEMBERS_PROGRAM`). Show the number selected against it and stop at the limit. "Select all eligible" is `invite_all_enrolled` or `all_eligible`, which cap themselves.
- A learner sees only the messages sent after they accepted. Show the "N people can see this" and "Not for emergencies" notices the group object gives you.
- In a program group, other learners see a learner by display name only (for example "Priya S."), never the full name, email or phone. Providers see full names. The learner can change the display name when accepting.
- The group name must not contain a learner's name or a blocked word (422). Do not put a person's name in a name suggestion.
- Invitations: the invited person gets an in-app notification and a `group_invitation` socket event, and decides in `GET /groups/invitations`.


## 7. Order of calls in the create screen

1. `GET /groups/trainings` to fill the Training picker.
2. After the user picks a Training: `GET /groups/trainings/{id}/learners?page=1&page_size=50` (and `&q=` while they type). Keep the selected `user_id`s on the client. Disable rows with `can_invite: false`.
3. `GET /groups/trainings/{id}/providers` for the Provider picker.
4. `POST /groups` with the selected ids, or with `invite_all_enrolled: true` for "Select all eligible".
5. Open the new group with the returned `id`.
6. Later: `GET /groups/trainings/{id}/learners?not_in_group=true` and `POST /groups/{id}/members/invite-enrolled`.


## 8. For the backend team

- No migration is needed. The link type is stored in the existing `conversations.context_type` column.
- New setting: `GROUP_PROVIDER_REQUIRES_TRAINING_ASSIGNMENT` (default `true`).
- Code: `app/services/group_chat_training.py` (the training rules), `app/services/group_chat_service.py` (`create_group`, `invite_members`, `invite_enrolled`), routes in `app/api/v1/endpoints/group_chat.py`, tests in `tests/test_group_chat_training.py`.
- The roster writes `TrainingEnrolment.user_id` when it can work it out from the clinic's member list. This uses the same identity service lookups as the training notifications.
- Limits that are not built: the backend does not close a training group when the Training ends (the owner closes it), and it does not invite late enrollees automatically.


## 9. Integration answers

### 9.1 Provider verification (403 "Your registration has not been verified yet")

Group chat keeps its own list of verified Providers (`verified_providers`). It is separate from anything else: no other module in this backend feeds it, and being a Provider of the clinic, or verified somewhere else, does not put anyone on it. A Provider must be on this list to create a group, own one, be made an admin, or be invited into a training group as a Provider.

- **Who adds them:** the clinic's Enterprise Admin (own clinic only) or the Super Admin, with `POST /groups/verified-providers`. Only `user_id` is required.
  ```json
  { "user_id": "<the Provider's user id>", "registration_number": "MH-12345", "registration_council": "State Medical Council" }
  ```
  `GET /groups/verified-providers` lists them; `DELETE /groups/verified-providers/{user_id}` revokes. Revoking does not remove groups the Provider already owns.
- **Does the backend do it automatically?** No. Every Provider who should use group chat is added once.
- **UI:** yes, a small screen for the Enterprise Admin (and Super Admin) is needed: a list from `GET /groups/verified-providers` and an "Add Provider" form that takes the Provider's user id, with the registration number and council as optional text. If you prefer to skip the whole step, the server setting `GROUP_REQUIRE_VERIFIED_PROVIDER=false` makes every Provider of the clinic count as verified for creating groups (they are then also usable as admins and owners only if they are on the list; keep the list for those).
- **To unblock testing now:** call `POST /groups/verified-providers` as the Enterprise Admin with the Enzo Provider's user id.

### 9.2 One learner in several groups of one Training

Yes. A learner can be a member of any number of groups of the same Training. Nothing on the server stops it.

The `already_invited` / `already_member` reason you saw was the roster's way of showing the learner's state. It now depends on what you are doing:
- `GET /groups/trainings/{id}/learners` **without** `group_id` is for a **new** group: `can_invite` is `true` for every eligible learner with an account, and `group_state` / `group_ids` still say where the learner already is. Use them as information ("already in 1 group"), not as a block.
- With `group_id=<the group you are filling>`, `can_invite` is about that group: `already_member` or `already_invited` only when they are in **that** group.
- `not_in_group=true` still means "not in any group of this Training" (or in `group_id` when it is given).
- `POST /groups/{id}/members/invite-enrolled` with `all_eligible` only skips people who are already in or invited to **that** group.

### 9.3 Names of pending invitations

`GET /groups/{id}/members` returns the name of a pending invitee to the owner and admins (only they see pending rows). The server uses the identity service name; when it has none, a training group uses the name on the learner's enrolment, then any name the person has used in chat. `name` is `null` only when no name is known anywhere. Learners never see pending invitations.

### 9.4 Group picture (avatar)

1. Upload the image with `POST /api/v1/uploads/` (multipart form: `file` = the image, `folder` = `group-avatars`). Image files up to 100 MB are accepted. The answer is `{ "url": "/api/v1/uploads/group-avatars/ab12cd.png", "name": "...", "size": 12345, "type": "image/png" }`.
2. Send that `url` as `avatar_url` in `POST /groups` or `PATCH /groups/{id}`. Show the picture by loading the same `url` from the API host (the download needs no token).
3. `avatar_url` accepts the upload `url` or an `http(s)://` address; anything else is 422. An empty string removes the picture.

The attachment API (`POST /attachments/upload`) is for files sent in messages. It needs an existing conversation, so it cannot be used when the group is being created.

### 9.5 Reopen

`POST /groups/{id}/reopen`
```json
{ "reason": "More sessions were added" }
```
`reason` is required, 3 to 255 characters (422 otherwise). Allowed: the group **owner**, or compliance (the clinic's Enterprise Admin for that clinic's groups, or the Super Admin). A group admin or a member gets 403. The group must be closed (409 "The group is not closed" if not). The answer is the group, open again. The retention clock stops and all members are notified (`group_reopened`).

### 9.6 Permissions in the group details

`GET /groups/{id}` (and every group object, including `GET /groups` and the compliance list) now has `permissions`, worked out by the server from the caller's role, the kind of member, whether the group is open and the posting rules. Use it instead of reading `my_role`.

| Field | True when |
|---|---|
| can_post | the caller is a member, the group is open, they are not muted, and posting is allowed for them (providers-only posting makes it false for learners) |
| can_edit_group | owner or admin, group open (name, description, picture) |
| can_change_settings | owner or admin, group open (who can send messages) |
| can_invite | owner or admin, group open |
| can_view_members | active member |
| can_view_pending_invitations | owner or admin |
| can_remove_members | owner or admin, or compliance |
| can_make_admin | owner or admin, or compliance |
| can_remove_admin | owner or compliance (a reason is required) |
| can_mute_members | owner or admin |
| can_hide_messages | owner or admin |
| can_pin_messages | a Provider who is a member |
| can_close | owner or admin, group open |
| can_reopen | the group is closed and the caller is the owner or compliance |
| can_transfer_ownership | owner |
| can_reassign_owner | compliance |
| can_leave | any active member except the owner |
| can_view_audit | owner or compliance |
| can_set_legal_hold | compliance |
| can_report | active member |
| can_mute_group | active member (mute or pin the group for myself) |

`my_role` and `my_kind` are still returned. When the caller is compliance and not a member (the Enterprise Admin looking at a Provider's group) only the compliance fields are true: `can_remove_members`, `can_make_admin`, `can_remove_admin`, `can_reopen` (if closed), `can_reassign_owner`, `can_view_audit`, `can_set_legal_hold`.

### 9.7 Close

`POST /groups/{id}/close` takes **no required body**. Call it with no body, with `{}`, or with a reason:
```json
{ "reason": "Training ended" }
```
`reason` is optional, up to 255 characters. Allowed: the owner or an admin of the group. The group becomes read-only, the retention period starts, members are notified, and the answer is the group with `status: "closed"`, `closed_at` and `closed_reason`. Closing a group that is already closed gives 409. A member gets 403.

