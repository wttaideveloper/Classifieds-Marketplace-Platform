"""Chat message pushes group by conversation id on Android (collapse key + tag) and iOS (thread-id)."""

from unittest.mock import patch
from uuid import uuid4

from firebase_admin import _messaging_encoder as fcm_utils

from app.services import chat_notification_service as chat
from app.services import firebase_push_service as push

DATA = {"type": "chat_message", "conversationId": "c", "messageId": "m", "custom": "kept"}


def encoded(message) -> dict:
    """The JSON the Firebase Admin SDK would send for this message."""
    return fcm_utils.MessageEncoder().default(message)


def build(conversation_id=None, data=None):
    return push.build_push_message(
        "device-token", title="New message", body="Hi", payload_data=dict(data or DATA), conversation_id=conversation_id
    )


def test_chat_push_sets_android_collapse_key_and_tag_and_ios_thread_id():
    cid = str(uuid4())
    payload = encoded(build(cid))

    assert payload["android"]["collapse_key"] == cid
    assert payload["android"]["notification"]["tag"] == cid
    assert payload["apns"]["payload"]["aps"]["thread-id"] == cid
    # existing behaviour untouched
    assert payload["android"]["priority"] == "high"
    assert payload["notification"] == {"title": "New message", "body": "Hi"}
    assert payload["data"] == DATA
    assert payload["token"] == "device-token"


def test_different_conversations_get_different_ids_and_the_same_conversation_the_same_id():
    first, second = str(uuid4()), str(uuid4())
    ids = lambda p: (  # noqa: E731
        p["android"]["collapse_key"], p["android"]["notification"]["tag"], p["apns"]["payload"]["aps"]["thread-id"]
    )
    a1, a2, b = ids(encoded(build(first))), ids(encoded(build(first))), ids(encoded(build(second)))
    assert a1 == a2 == (first, first, first)
    assert b == (second, second, second) and b != a1


def test_pushes_without_a_conversation_have_no_grouping_fields():
    payload = encoded(build(None, {"type": "event_approved", "event_id": "e"}))

    assert "collapse_key" not in payload["android"]
    assert "notification" not in payload["android"]
    assert "apns" not in payload
    assert payload["android"]["priority"] == "high"
    assert payload["data"] == {"type": "event_approved", "event_id": "e"}


def test_send_passes_the_conversation_id_to_every_device(monkeypatch):
    sent = []
    monkeypatch.setattr(push, "_ensure_firebase", lambda: (True, "proj", None))
    with patch("firebase_admin.messaging.send", side_effect=lambda m: sent.append(encoded(m))):
        result = push.send_push_to_tokens(
            ["t1", "t2"], title="T", body="B", data=DATA, conversation_id="conv-1"
        )
    assert result.sent_count == 2
    assert {m["android"]["collapse_key"] for m in sent} == {"conv-1"}
    assert {m["apns"]["payload"]["aps"]["thread-id"] for m in sent} == {"conv-1"}


def test_the_chat_message_dispatch_uses_the_conversation_id_not_message_or_sender(monkeypatch):
    conversation_id, message_id = uuid4(), uuid4()

    class Device:
        token = "device-token"

    monkeypatch.setattr(chat.chat_repo, "get_active_device_tokens", lambda db, user_id: [Device()])
    with patch.object(chat, "send_push_to_tokens") as send:
        chat._dispatch_push_notification(None, uuid4(), "Title", "Body", conversation_id, message_id)

    kwargs = send.call_args.kwargs
    assert kwargs["conversation_id"] == str(conversation_id) != str(message_id)
    assert kwargs["data"] == {"type": "chat_message", "conversationId": str(conversation_id), "messageId": str(message_id)}
    assert kwargs["title"] == "Title" and kwargs["body"] == "Body"


def test_other_notification_types_do_not_pass_a_conversation_id():
    from app.services import notification_delivery_service as delivery

    import inspect

    source = inspect.getsource(delivery.deliver_notification_to_users)
    assert "conversation_id" not in source
