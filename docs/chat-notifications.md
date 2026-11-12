# Chat and group message notifications

What a new-message notification says, for 1:1 chats and groups. The same content is used for **push** (FCM / APNs `notification` + `data`), the **inbox**
(`GET /api/v1/users/me/notifications`: `title`, `message`, `metadata`), the chat inbox row and the Socket.IO `notification` event.

## Display

| | `title` | `body` (push) / `message` (inbox) |
|---|---|---|
| 1:1 chat | the sender's name (the conversation subject if the name is unknown) | a preview of what was sent |
| Program group (training and event groups too) | the group's name | a preview of what was sent |
| Care group | `New message` | empty (unchanged: see below) |

Preview of what was sent (never empty when there is content):

| Content | Preview |
|---|---|
| Text | the first 100 characters (`CHAT_NOTIFICATION_PREVIEW_CHARS`), whitespace squashed, cut with `...` |
| Image | its caption, or `Sent a photo` |
| File | its caption, or `Sent {file name}` (`Sent a file` when the name is unknown; names are cut to 60 characters) |
| Audio | `Sent a voice note` |
| Video | its caption, or `Sent a video` |
| Anything else | `Sent an attachment` |

## `data` (push) and `metadata` (inbox)

All values are strings. Both always have:

| Key | Value |
|---|---|
| `category` | `chat_message` (1:1) or `group_message` (group) |
| `type` | the same as `category` |
| `conversation_id`, `message_id` | the ids to open the chat |
| `preview` | the same text as the body |
| `sender_name` | the sender (left out when unknown). In a group a patient is shown by display name (`Priya S.`), a provider by full name. Never an e-mail address |
| `attachment_type` | `none`, `image`, `file`, `audio`, `video` or `other` |

The push `data` also keeps `conversationId` and `messageId` (what older app versions read).

```json
{ "notification": { "title": "event gccc", "body": "Hey, are we still meeting at 5?" },
  "data": { "category": "group_message", "type": "group_message", "conversation_id": "f48117a8-2679-48be-abf3-bf733b47ccab",
            "message_id": "6e58ae50-d9e4-4f1e-b258-ac4917452c7c", "preview": "Hey, are we still meeting at 5?", "sender_name": "Ravi",
            "attachment_type": "none", "conversationId": "f48117a8-2679-48be-abf3-bf733b47ccab", "messageId": "6e58ae50-d9e4-4f1e-b258-ac4917452c7c" } }
```

## Care groups, and the switches

A **care group** is one patient's care team, so its messages are clinical. A preview would put them on a lock screen, so a care group still shows only
`New message` with an empty body. Its `data` now also has `category` and the snake_case ids; nothing else. Settings:

* `GROUP_CARE_NOTIFICATION_PREVIEW_ENABLED` (default `false`): `true` gives care groups the same title and preview as program groups.
* `GROUP_NOTIFICATION_PREVIEW_ENABLED` (default `true`): `false` brings the earlier `New message` / empty body back for every group.
* `CHAT_NOTIFICATION_PREVIEW_CHARS` (default `100`).

Not changed: muted members and tags (a tag reaches a muted member only in a care group), no email or SMS for group messages, and the invitation
notification (it still has no group name). 1:1 SMS (when enabled) now reads `{sender}: {preview}`.
