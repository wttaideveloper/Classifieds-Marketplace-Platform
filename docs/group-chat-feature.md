# Group Chat: Feature Document

This document describes the group chat feature for the mobile customer, the group admin (Enterprise Owner or provider) and the Super Admin. It covers what a group is, the rules, the user flows, the fields, the APIs and socket events, and the backend work needed.

Group chat does not exist in the code today. Chat only has one-to-one style conversations (types `standard`, `preview` and `booking`). I looked at how chat works now and wrote down what can be reused and what has to change. I did not find a written requirement for groups, so section 1 lists what I assumed. Please correct it before development starts.


## 1. What I assumed

1. A group is one conversation with three or more people and a name. Everyone in it sees every message.
2. Groups are created by an Enterprise Owner or a provider for their own business, for example "Yoga batch Oct 2026" or "Event volunteers". A customer cannot create a group in the first version.
3. The members are the business's own staff and its customers (learners, attendees, buyers).
4. The first version is groups created by hand. Groups that are created automatically for a training batch or an event come later (section 10).
5. The chat features that already exist (text, images, documents, audio, video, typing, read status, online status, push notifications) should work the same in groups.

If groups are also meant for customers to chat with each other, or for staff-only chat across businesses, the rules in section 4 change.


## 2. What a group chat is

- It has a name, an optional description and an optional picture.
- It has a list of members. Each member has a role in the group: owner, admin or member.
- The owner is the person who created it. The owner and the admins can add and remove members, change the name and close the group.
- Every member can read and send messages (unless the group is closed or set to "admins only", see section 12), leave the group, and mute it.
- A member who is removed or leaves does not receive new messages and cannot open the group any more.


## 3. What already works and what has to change

The chat code already stores a list of participants for each conversation, so the base is there. This table shows where groups need more.

| Part of chat | How it works today | What groups need |
|---|---|---|
| Conversation type | standard, preview, booking | a new type `group` |
| Participants | any number of rows, with a role of customer, provider or admin | roles owner, admin, member. The role list in the API schema is fixed and has to be widened |
| Creating | `POST /conversations`. If an open conversation with exactly the same people exists, it is returned instead of a new one | a group must always be new, even with the same people. Skip that check for groups |
| Name | `subject` | use `subject` as the group name, add description and picture |
| Sending a message | every participant gets it in real time through the conversation room | no change |
| Message sender | the message has `sender_id` only | add the sender's name (and picture) to every message, because the app has to show who wrote it |
| Read status | one record per message per reader, plus `last_read_at` per participant | works for many readers. The screen should show "seen by 5" instead of a single tick |
| Unread count | based on `last_read_at` of each participant | no change |
| Typing | everyone in the room receives `typing` with a user id | the app has to show "Asha and 2 others are typing". Needs the user's name |
| Notifications | each participant except the sender gets an in-app notification, push, email and SMS according to their global settings | add a mute per group, and a limit on the group size (section 5) |
| Close, reopen, archive | any participant can do it, and it changes the conversation for everyone | in a group only the owner or an admin can close it. "Archive" must be per member |
| Edit and delete a message | the sender, or a user with role admin or super_admin | in a group the sender and the group admins. See section 11 about the current check |
| Inbox customer name | `customer_name` on the list and detail | must be null for a group. The group name is in `subject` |
| Provider assignment | one assigned provider per conversation | does not apply to groups |
| Preview limit | preview conversations use up the free message count | groups do not use it |
| System messages | message type `system` exists | use it for "Asha added Ravi", "Ravi left" |
| Admin screens | the Super Admin can list and export conversations | groups should show there too |


## 4. Users

### 4.1 Mobile customer (a member of a group)

What a member can do

- See the groups they are in, in the same inbox as their other chats, with the group name, last message, unread count and the picture.
- Open a group and read the messages. Each message shows who sent it.
- Send text, images, documents, audio and video.
- See who is typing, and how many people have seen a message.
- See the member list with names, roles and who is online.
- Mute the group (no push or email for it, the unread count still grows).
- Leave the group.
- Edit or delete their own messages.

What a member cannot do

- Add or remove people, rename the group or close it (only the owner and admins).
- Open a group they were removed from.

Flow: a customer is added to a group

1. The group admin adds the customer.
2. The customer gets a notification: "You were added to Yoga batch Oct 2026".
3. The group appears in the inbox. Tapping the notification opens the group.
4. A system message "Asha added Ravi" is shown in the chat.
5. Whether Ravi can read the earlier messages is an open decision (section 12).

Flow: a customer leaves

1. The customer opens the group info and taps "Leave group", then confirms.
2. The group disappears from their inbox. The other members see "Ravi left".
3. If the last owner leaves, the oldest admin becomes the owner. If there is no admin, the oldest member does.

Screens for the mobile team

1. Inbox row for a group: picture or initials, name, last message with the sender's name ("Asha: see you at 6"), time, unread count, muted icon.
2. Group chat screen: header with the name and "12 members, 3 online", messages with the sender's name and picture, system messages in the middle, "seen by" on the last message, typing line.
3. Group info: name, description, picture, member list, mute switch, leave button. Admins also see add member, remove member, make admin, edit and close.
4. Add members screen (admins): search the people of the business.

### 4.2 Enterprise Owner and providers (group admin)

The Enterprise Owner can create groups. Whether providers can too is open (section 12). The person who creates a group becomes its owner and can make other members admins.

What the group admin needs

- Create a group with a name, optional description and picture, and a first list of members.
- Add and remove members. Only people of their own business can be added, and customers only if they have a relation to the business (for example enrolled in a training or registered for an event).
- Make a member an admin, or take it back.
- Rename the group, change the picture and description.
- Close the group (no new messages, history stays) and reopen it.
- Delete a message of anyone in the group.
- Mute the group.

Flow: owner creates a group

1. Owner opens Chats and taps "New group".
2. Enters the name, picks the members from a list, optionally adds a picture.
3. The server creates the group. Every member gets a notification and a system message "Owner created the group".
4. The owner can start writing. The group is in the inbox of every member.

Flow: owner removes a member

1. Owner opens the group info, taps the member, taps "Remove".
2. The member is taken out of the group immediately: the group disappears from their inbox, and if they have it open they get a message saying they were removed. The others see "Ravi was removed".

Screens for the web team

1. Group list with a search and a "New group" button.
2. Group chat view (same as the 1:1 chat with the sender names).
3. Group settings panel: details, members table with role and Remove, add members dialog.

### 4.3 Super Admin

The Super Admin does not take part in the groups. What they need is control of the platform.

- See all groups of all businesses in the existing chat admin pages, with the business, name, number of members and last activity.
- Open a group and export its messages (the export API exists for conversations).
- Close a group or remove a member after a complaint.
- Delete an abusive message.
- See the numbers: groups, members and messages per business.

Whether the Super Admin may read the messages of a private group, and how that is recorded, is an open decision (section 12).


## 5. Rules

Membership

1. A group has at least 2 members at all times. A group of 2 stays a group.
2. A group can have up to 100 members at first. The limit is low on purpose, because every message creates a notification record, a push and possibly an email for every member. We can raise it after we test the load.
3. A person can only be added once. Adding a person who is already a member is ignored.
4. Only the owner and the admins can add and remove members. An admin cannot remove the owner, and cannot remove another admin (only the owner can).
5. Members of a group must belong to the same business as the group, except customers who are linked to that business.
6. The creator is the owner. The owner can hand the ownership to an admin. If the owner leaves, the ownership passes on as described in 4.1.
7. A removed member can be added again later.

Messages

8. Only members can read or send messages. A message is sent to the room of the group, and only people inside the room receive it.
9. The existing message rules apply: the group must be open, not read-only, and the text can be edited by the sender.
10. A member can delete their own message. The owner and the admins can delete any message in their group. A deleted message stays as "This message was deleted".
11. Message history: new members see messages from the moment they were added (the default, see section 12).
12. When someone is removed or leaves, the messages they wrote stay in the group.

Notifications

13. Every message notifies all members except the sender, according to their notification settings, unless they muted the group.
14. A muted group still raises the unread count but sends no push, email or SMS.
15. Being added to a group, being removed and a role change create a notification.

Closing

16. When a group is closed nobody can send messages. Members can still read the history and leave.
17. Archive is per person: archiving hides the group in my inbox only.


## 6. Fields

Conversation (additions to the existing table `conversations`)

| Field | Notes |
|---|---|
| conversation_type | new value `group` |
| subject | the group name, required for a group, up to 255 characters |
| description | new, optional, up to 500 characters |
| avatar_url | new, optional, uploaded picture |
| max_members | new, default 100 |
| created_by | the owner when created |
| context_type, context_id | already exist. For automatic groups later: `training` or `event` with its id |
| tenant_id | the business that owns the group, required for a group |

Participant (additions to `conversation_participants`)

| Field | Notes |
|---|---|
| role | new values `owner`, `admin`, `member` (today customer, provider, admin) |
| added_by | new, who added this member |
| muted | new, true or false |
| archived | new, true or false, per member |
| joined_at | exists. Used for "history from the day you joined" |
| left_at | new, filled when the person leaves or is removed. A member with left_at is not a member |
| last_read_at | exists, used for the unread count |

Message (additions to the message response)

| Field | Notes |
|---|---|
| sender_name | new, the display name of the sender, never an email |
| sender_avatar_url | new, optional |
| message_type | `system` is used for join, leave and rename lines |

System message text is stored in `content`. It is built on the server, for example "Asha added Ravi".


## 7. API list

All paths start with `https://chat.wisdomtooth.tech/api/v1`. The first table is what we already have and reuse without a change in the path.

Already available

| What | Call |
|---|---|
| List my conversations (groups included) | GET /conversations |
| Open one conversation | GET /conversations/{id} |
| Read the messages | GET /conversations/{id}/messages |
| Mark the conversation as read | PATCH /conversations/{id}/read |
| Send a message | POST /messages (or the socket event `send_message`) |
| Edit or delete a message | PATCH and DELETE /messages/{id} |
| Who has read a message | GET /messages/{id}/read-status |
| Search messages and conversations | GET /messages/search, GET /conversations/search |
| Upload an attachment | the attachment API |
| Typing | the socket events, or the typing API |
| Online status | GET /presence/online |
| Unread count and notification settings | the chat notification APIs |
| Admin lists and export | GET /chat/admin/conversations, GET /chat/admin/conversations/{id}/export |

New

| What | Call | Who |
|---|---|---|
| Create a group | POST /conversations with `conversation_type: "group"`, `subject`, `description`, `participant_ids` (the existing endpoint, with the duplicate check skipped for groups) | owner, provider |
| Change name, description, picture | PATCH /conversations/{id} | group owner or admin |
| List the members | GET /conversations/{id}/members | any member |
| Add members | POST /conversations/{id}/members with `user_ids` | owner, admin |
| Remove a member | DELETE /conversations/{id}/members/{user_id} | owner, admin |
| Change a member's role | PATCH /conversations/{id}/members/{user_id} with `role` | owner |
| Leave | POST /conversations/{id}/leave | any member |
| Mute or unmute | PATCH /conversations/{id}/mute with `muted` | any member |
| Archive for me | the existing archive API, but per member for groups | any member |
| Close and reopen | the existing close and reopen APIs, owner and admin only for groups | owner, admin |
| People who can be added | GET /chat/groups/candidates?q=&context_type=&context_id= | owner, admin |

Sample: create a group

```json
POST /conversations
{
  "conversation_type": "group",
  "subject": "Yoga batch Oct 2026",
  "description": "Class updates and questions",
  "participant_ids": [
    { "user_id": "550e8400-e29b-41d4-a716-446655440020", "role": "member" },
    { "user_id": "550e8400-e29b-41d4-a716-446655440030", "role": "member" }
  ]
}
```

Response 201 (the usual conversation response plus)

```json
{
  "id": "…",
  "conversation_type": "group",
  "subject": "Yoga batch Oct 2026",
  "description": "Class updates and questions",
  "avatar_url": null,
  "member_count": 3,
  "my_role": "owner",
  "customer_name": null,
  "participants": [ { "user_id": "…", "role": "owner", "name": "Priya" } ]
}
```

Sample: member list

```json
GET /conversations/{id}/members
{
  "members": [
    { "user_id": "…", "name": "Priya", "role": "owner", "online": true, "joined_at": "2026-10-08T09:00:00" },
    { "user_id": "…", "name": "Asha", "role": "member", "online": false, "joined_at": "2026-10-08T09:00:00" }
  ],
  "total": 2
}
```

Errors: 403 when a member without the right tries to add or remove, 404 when the group does not exist or the person is not a member, 409 when the group already has the maximum members, 422 for an empty name or a group with fewer than 2 members.


## 8. Realtime (socket) events

Already working and used as they are: `join_room`, `leave_room`, `send_message`, `typing_start`, `typing_stop`, `mark_read`, and from the server `new_message`, `message_read`, `typing`, `message_updated`, `message_deleted`, `conversation_updated`, `user_online`, `user_offline`.

Group changes to make

- `typing` already carries the user id. Add the user's name so the app can write "Asha is typing".
- `message_read` for a group should carry the reader's id so the app can update "seen by".
- The server must put a newly added member's connected devices into the room, and take a removed member's devices out of it. Today a person joins a room only by asking for it, so without this a removed member who is still connected would keep receiving the messages.

New events

| Event | Sent to | Payload |
|---|---|---|
| `member_added` | the group room, and the new member's own room | conversation_id, user (id, name, role), added_by |
| `member_removed` | the group room, and the removed person's own room | conversation_id, user_id, removed_by |
| `member_left` | the group room | conversation_id, user_id |
| `member_role_changed` | the group room | conversation_id, user_id, role |
| `group_updated` | the group room | conversation_id, subject, description, avatar_url |

The removed person gets `member_removed` in their personal room, because they are no longer in the group room. The app uses it to close the open screen and remove the group from the inbox.


## 9. Notifications

We use the notification feed and the socket event `notification` that already exist. We do not add a new system.

| Category | Sent to | When |
|---|---|---|
| chat_message | every member except the sender and except those who muted the group | a new message (already works) |
| group_added | the person added | someone adds them |
| group_removed | the person removed | the owner or an admin removes them |
| group_role_changed | the person | they become admin or lose it |
| group_closed | all members | the group is closed |

The metadata of each notification has `conversation_id` and `category` so the mobile app can open the right group. For `group_removed` there is nothing to open, so the app only shows the message.

The notification for a message in a group says "Priya in Yoga batch Oct 2026: see you at 6" (the sender's name, then the group name).


## 10. Backend work, in order

1. Allow the type `group` and the new roles in the schemas. Do not reuse an existing conversation when the type is `group`.
2. Add the new columns (section 6) with one migration.
3. Add the sender's name to the message response and to the socket payloads. The name lookup used for `customer_name` can be reused.
4. Make `customer_name` null for groups.
5. The member APIs: add, remove, role change, leave, list, with the permission rules and the system messages.
6. Keep the socket rooms in sync when members are added or removed (section 8).
7. Close, reopen and archive: owner and admin only for close and reopen, and per member for archive.
8. Mute, and the notification changes (muted members, the sender's name in the text, the new categories).
9. The member limit, and a check that the people added belong to the business.
10. Message delete and edit rules for the group admins (and the fix in section 11).
11. The list of people who can be added.
12. Admin pages: groups in the Super Admin lists, export, remove member, delete message.
13. Later: groups created automatically for a training batch or an event (the fields `context_type` and `context_id` are already there), "admins only" groups, @mentions, pinned messages, reply to a message.

Items 1 to 8 are the minimum for a first version.


## 11. Problems in the current chat code that matter for groups

1. Edit and delete message check: the code allows the sender, or any user with the role `admin` or `super_admin`, and it does not check that the user is a member of the conversation or belongs to the same business. So an Enterprise Admin of one business could edit or delete a message in another business's chat. This should be fixed before groups, because groups have many more messages and admins.
2. Close, reopen and archive change the conversation for everyone, and any participant can do it. In a group, one member could close the chat for all.
3. Notification settings are global. There is no mute for one conversation.
4. Every message creates one notification record per member and may send an email and an SMS per member. This does not scale to large groups, so the 100 member limit and the mute are needed.
5. A member joins a socket room only by asking for it, and the server does not remove a person from a room when they are removed from the conversation.
6. `find_existing_conversation` returns an open conversation with exactly the same people. For a group this would give the wrong chat.
7. The participant role list in the API schema is fixed to customer, provider and admin.


## 12. Questions for the product owner

1. Who can create a group: only the Enterprise Owner, or providers too? Can a customer create a group?
2. Can customers talk to each other in a group, or is it always business and customers? Are there groups with customers only?
3. Should a new member see the messages sent before they joined?
4. Do we need "admins only" groups, where members can read but not write (for announcements)?
5. Is 100 members enough? What is the biggest group we expect, for example a whole event?
6. Should the Super Admin be able to read the messages of a private group? If yes, should the group members be told, and should each access be recorded?
7. When the owner leaves and nobody else is in the group, do we delete it or keep it closed?
8. Should a group be created automatically for every training batch and every event? If yes, who is in it, and does it close when the training or event ends?
9. Can members add other members, or only admins?
10. Do we keep the chat for a removed member, for example for a legal request? The messages stay in the database anyway, so the question is only whether the person can see them.
11. Should the group picture and name be public to people outside the group, for example in search? The default here is no.


## 13. Test checklist

- A group can be created with 2 or more members and a name. A group without a name, or with only the creator, is refused (422).
- Creating a second group with the same people creates a new group.
- The creator is the owner. The members see the group in their inbox and get a `group_added` notification.
- A message sent in the group reaches every member and nobody else. The sender's name is on the message.
- Unread counts are per member and drop to 0 after the member reads the group.
- "Seen by" lists the members who read the message.
- A normal member cannot add, remove, rename or close (403). An admin can. An admin cannot remove the owner or another admin.
- Removing a member: they stop receiving messages immediately, even if their app is open, their inbox no longer shows the group, and their old messages stay.
- Leaving works. If the owner leaves, the ownership moves to the oldest admin, then the oldest member.
- A muted member gets no push or email but the unread count grows.
- The 101st member is refused (409).
- A person from another business cannot be added.
- Closing a group blocks sending (400) but allows reading. Archive hides the group for one member only.
- A member of one group cannot read, edit or delete the messages of another group, and an admin of another business cannot either.
- `customer_name` is null on a group in the list and detail.
- The Super Admin sees the group in the admin list and can export it.
