"""What a chat or group message notification says: the title, the preview text and the push `data`.

One place for 1:1 chats and groups, for push (FCM / APNs), the notification inbox (`GET /users/me/notifications`) and the Socket.IO `notification`
event, so they always agree.

    title   1:1 -> the sender's name                    group -> the group's name
    body    a preview of what was sent (never empty when there is content):
              text                the first CHAT_NOTIFICATION_PREVIEW_CHARS characters (100 by default), cut with "..." when longer
              image               its caption, or "Sent a photo"
              file                its caption, or "Sent {file name}" (or "Sent a file" when the name is unknown)
              audio               "Sent a voice note"
              video               its caption, or "Sent a video"
              anything else       "Sent an attachment"
    data    all strings: category, type (= category), conversation_id, message_id, preview (= body), sender_name (when known),
            attachment_type (none | image | file | audio | video | other)

`category` / `type` is "chat_message" for a 1:1 chat and "group_message" for a group.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.config import settings

ONE_TO_ONE = "chat_message"
GROUP = "group_message"
GENERIC_TITLE = "New message"

_ATTACHMENT_KIND = {"image": "image", "document": "file", "file": "file", "audio": "audio", "voice": "audio", "video": "video"}


@dataclass
class Content:
    title: str
    body: str
    data: dict[str, str] = field(default_factory=dict)


def attachment_kind(message_type: str | None, attachment_type: str | None = None) -> str:
    """none | image | file | audio | video | other, from the stored attachment when there is one, else from the message type."""
    raw = (attachment_type or message_type or "").strip().lower()
    if raw in ("", "text", "system"):
        return "none"
    return _ATTACHMENT_KIND.get(raw, "other")


def _squash(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(limit - 3, 1)].rstrip() + "..."


def _file_label(file_name: str | None) -> str | None:
    name = _squash(file_name).replace("/", " ").replace("\\", " ").strip()
    return _cut(name, 60) if name else None


def preview_text(kind: str, content: str | None, file_name: str | None = None, limit: int | None = None) -> str:
    """The notification body for a message. Never empty."""
    limit = limit or settings.CHAT_NOTIFICATION_PREVIEW_CHARS
    text = _squash(content)
    if kind == "audio":
        return "Sent a voice note"
    if text:
        return _cut(text, limit)          # a text message, or the caption of an image / video / file
    if kind == "image":
        return "Sent a photo"
    if kind == "video":
        return "Sent a video"
    if kind == "file":
        label = _file_label(file_name)
        return f"Sent {label}" if label else "Sent a file"
    if kind == "other":
        return "Sent an attachment"
    return GENERIC_TITLE


def data_for(category: str, conversation_id, message_id, preview: str, sender_name: str | None, kind: str) -> dict[str, str]:
    data = {
        "category": category, "type": category, "conversation_id": str(conversation_id), "message_id": str(message_id),
        "preview": preview, "attachment_type": kind,
    }
    if sender_name:
        data["sender_name"] = sender_name
    return data


def build(category: str, *, title: str, conversation_id, message, attachment=None, sender_name: str | None = None) -> Content:
    """`message` needs id, content and message_type; `attachment` (optional) needs file_name and attachment_type."""
    kind = attachment_kind(getattr(message, "message_type", None), getattr(attachment, "attachment_type", None))
    preview = preview_text(kind, getattr(message, "content", None), getattr(attachment, "file_name", None))
    return Content(
        title=_squash(title) or GENERIC_TITLE, body=preview,
        data=data_for(category, conversation_id, message.id, preview, _squash(sender_name) or None, kind),
    )


def sender_name_for(db, conversation, sender_id, current_user: dict | None = None) -> str | None:
    """The sender's name as the identity service and the platform's own records know it. Never an e-mail address; None when unknown."""
    try:
        from app.services import chat_user_names

        return chat_user_names.resolve_display_names(
            [sender_id], tenant_ids=[getattr(conversation, "tenant_id", None)], current_user=current_user, db=db,
        ).get(str(sender_id))
    except Exception:
        return None
