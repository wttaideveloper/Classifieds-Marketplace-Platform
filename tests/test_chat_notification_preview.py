"""Chat and group message notifications: title, preview and `data` for push and the inbox.

1:1 -> the sender's name and a preview; program groups (training and event groups too) -> the group name, the sender's display name and a preview;
care groups -> still only "New message" unless GROUP_CARE_NOTIFICATION_PREVIEW_ENABLED. Real routes / services on an in-memory database; push and the
platform feed are captured, nothing is sent.
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.config import settings
from app.models.chat_model import ChatAttachment, ChatNotification, Conversation, ConversationParticipant, DeviceToken
from app.schemas.chat_schema import MessageCreate
from app.services import chat_notification_content as notice
from app.services import chat_service
from tests.group_chat_support import TENANT, joined_group, say, uid, world  # noqa: F401

API = "/api/v1/groups"


# ---- the rules, on their own -------------------------------------------------------------------------------------------------------

def test_the_preview_of_each_kind_of_content():
    p = notice.preview_text
    assert p("none", "Hey, are we still meeting at 5?") == "Hey, are we still meeting at 5?"
    assert p("none", "  two\n\n lines   here ") == "two lines here"
    assert p("image", None) == "Sent a photo" and p("image", "") == "Sent a photo"
    assert p("image", "Look at this") == "Look at this"                              # a caption wins
    assert p("file", None, "report.pdf") == "Sent report.pdf" and p("file", None, None) == "Sent a file"
    assert p("file", "The signed copy", "report.pdf") == "The signed copy"
    assert p("audio", None) == "Sent a voice note" and p("audio", "ignored") == "Sent a voice note"
    assert p("video", None) == "Sent a video" and p("video", "Warm-up") == "Warm-up"
    assert p("other", None) == "Sent an attachment"
    assert p("none", None) == "New message"                                           # never empty


def test_a_long_text_is_cut_to_the_preview_length(monkeypatch):
    text = "word " * 60
    out = notice.preview_text("none", text)
    assert len(out) <= settings.CHAT_NOTIFICATION_PREVIEW_CHARS and out.endswith("...")
    monkeypatch.setattr(settings, "CHAT_NOTIFICATION_PREVIEW_CHARS", 20)
    assert len(notice.preview_text("none", text)) <= 20


def test_a_long_file_name_is_shortened_and_path_characters_removed():
    out = notice.preview_text("file", None, "a/b\\" + "x" * 200 + ".pdf")
    assert out.startswith("Sent a b") and len(out) <= len("Sent ") + 60


def test_attachment_kinds():
    k = notice.attachment_kind
    assert [k("text"), k("system"), k(None)] == ["none", "none", "none"]
    assert [k("image"), k("document"), k("audio"), k("video"), k("sticker")] == ["image", "file", "audio", "video", "other"]
    assert k("text", "image") == "image" and k("document", "audio") == "audio"        # the stored attachment decides


def test_data_is_all_strings_with_category_equal_to_type():
    msg = SimpleNamespace(id=uuid4(), content="Hi", message_type="text")
    data = notice.build(notice.GROUP, title="event gccc", conversation_id=uuid4(), message=msg, sender_name="Ravi").data
    assert set(data) == {"category", "type", "conversation_id", "message_id", "preview", "sender_name", "attachment_type"}
    assert all(isinstance(v, str) for v in data.values()) and data["category"] == data["type"] == "group_message" and data["attachment_type"] == "none"
    assert "sender_name" not in notice.build(notice.ONE_TO_ONE, title="x", conversation_id=uuid4(), message=msg).data


# ---- groups ----------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def program(world):
    p = world.people
    group = joined_group(world, kind="program", members=[p.mehta, p.priya, p.ravi], name="event gccc")
    world.as_user(p.rao).patch(f"{API}/{group['id']}/settings", json={"admins_only_posting": False})
    world.db.add(DeviceToken(user_id=uid(p.priya), token="tok-priya", platform="android", is_active=True))
    world.db.commit()
    return group


@pytest.fixture
def care(world):
    p = world.people
    group = joined_group(world, kind="care", members=[p.mehta, p.priya], name="Knee care team")
    world.db.add(DeviceToken(user_id=uid(p.priya), token="tok-priya", platform="android", is_active=True))
    world.db.commit()
    return group


def feed_of(world):
    return [n for n in world.notifications if n["category"] == "group_message"]


def push_of(world):
    return [x for x in world.pushes if "tokens" in x]


def test_a_program_group_message_has_the_group_name_sender_and_preview_in_push_and_inbox(world, program):
    p = world.people
    world.notifications.clear(), world.pushes.clear()

    assert say(world, p.rao, program["id"], "Hey, are we still meeting at 5?").status_code == 201

    [feed] = feed_of(world)
    assert feed["title"] == "event gccc" and feed["message"] == "Hey, are we still meeting at 5?" and feed["channels"] == ["in_app"]
    meta = feed["metadata"]
    assert meta["category"] == meta["type"] == "group_message" and meta["conversation_id"] == program["id"] and meta["message_id"]
    assert meta["preview"] == "Hey, are we still meeting at 5?" and meta["sender_name"] == "Dr Rao" and meta["attachment_type"] == "none"
    [push] = push_of(world)
    assert push["title"] == "event gccc" and push["body"] == "Hey, are we still meeting at 5?"
    data = push["data"]
    assert data["category"] == data["type"] == "group_message" and data["conversation_id"] == program["id"] == data["conversationId"]
    assert data["message_id"] == data["messageId"] and data["preview"] == push["body"] and data["sender_name"] == "Dr Rao" and data["attachment_type"] == "none"
    assert all(isinstance(v, str) for v in data.values())
    row = world.db.query(ChatNotification).filter_by(user_id=uid(p.priya)).order_by(ChatNotification.created_at.desc()).first()
    assert row.title == "event gccc" and row.body == "Hey, are we still meeting at 5?" and row.data["preview"] == row.body


def test_a_patient_is_named_by_display_name_never_by_full_name(world, program):
    p = world.people
    world.notifications.clear(), world.pushes.clear()

    say(world, p.priya, program["id"], "Is the class still on?")

    [feed] = feed_of(world)
    assert feed["metadata"]["sender_name"] == "Priya S." and "Sharma" not in str(feed) and "Sharma" not in str(push_of(world))


def test_an_attachment_message_says_what_was_sent(world, program):
    p = world.people
    gid = program["id"]
    file_row = ChatAttachment(conversation_id=uuid4(), uploaded_by=uid(p.rao), file_name="report.pdf", file_path="x", mime_type="application/pdf",
                              file_size=1, attachment_type="document")
    photo = ChatAttachment(conversation_id=uuid4(), uploaded_by=uid(p.rao), file_name="a.jpg", file_path="x", mime_type="image/jpeg", file_size=1, attachment_type="image")
    voice = ChatAttachment(conversation_id=uuid4(), uploaded_by=uid(p.rao), file_name="v.m4a", file_path="x", mime_type="audio/mp4", file_size=1, attachment_type="audio")
    clip = ChatAttachment(conversation_id=uuid4(), uploaded_by=uid(p.rao), file_name="c.mp4", file_path="x", mime_type="video/mp4", file_size=1, attachment_type="video")
    for row in (file_row, photo, voice, clip):
        row.conversation_id = __import__("uuid").UUID(gid)
        world.db.add(row)
    world.db.commit()

    def send(row, message_type, text=None):
        world.notifications.clear(), world.pushes.clear()
        resp = say(world, p.rao, gid, text, message_type=message_type, attachment_id=str(row.id))
        assert resp.status_code == 201, resp.text
        [feed] = feed_of(world)
        [push] = push_of(world)
        assert push["body"] == feed["message"] == feed["metadata"]["preview"] == push["data"]["preview"]
        return feed["message"], feed["metadata"]["attachment_type"], push["data"]["attachment_type"]

    assert send(file_row, "document") == ("Sent report.pdf", "file", "file")
    assert send(photo, "image") == ("Sent a photo", "image", "image")
    assert send(photo, "image", "Look at this") == ("Look at this", "image", "image")
    assert send(voice, "audio") == ("Sent a voice note", "audio", "audio")
    assert send(clip, "video") == ("Sent a video", "video", "video")


def test_a_care_group_still_shows_only_new_message(world, care):
    p = world.people
    world.notifications.clear(), world.pushes.clear()

    say(world, p.rao, care["id"], "your blood test result is ready")

    [feed] = feed_of(world)
    assert feed["title"] == "New message" and feed["message"] == "" and set(feed["metadata"]) == {"conversation_id", "message_id", "category"}
    [push] = push_of(world)
    assert push["title"] == "New message" and push["body"] == "" and "blood" not in str(push) and "Knee" not in str(push)
    assert push["data"]["category"] == push["data"]["type"] == "group_message" and push["data"]["conversation_id"] == care["id"]    # the ids are still there


def test_the_care_preview_can_be_switched_on(world, care, monkeypatch):
    monkeypatch.setattr(settings, "GROUP_CARE_NOTIFICATION_PREVIEW_ENABLED", True)
    world.notifications.clear()

    say(world, world.people.rao, care["id"], "your blood test result is ready")

    [feed] = feed_of(world)
    assert feed["title"] == "Knee care team" and feed["message"] == "your blood test result is ready"


def test_group_previews_can_be_switched_off(world, program, monkeypatch):
    monkeypatch.setattr(settings, "GROUP_NOTIFICATION_PREVIEW_ENABLED", False)
    world.notifications.clear(), world.pushes.clear()

    say(world, world.people.rao, program["id"], "hello team")

    [feed] = feed_of(world)
    assert feed["title"] == "New message" and feed["message"] == "" and push_of(world)[0]["body"] == ""


def test_the_realtime_notification_event_carries_the_same_content(world, program):
    import asyncio
    from uuid import UUID

    from app.realtime import emitters

    p = world.people
    captured = []

    async def fake_emit(user_id, payload):
        captured.append((user_id, payload))

    original = emitters.emit_notification
    emitters.emit_notification = fake_emit
    try:
        asyncio.run(emitters.notify_participants_new_message(world.db, UUID(program["id"]), p.rao["id"],
                                                             {"id": str(uuid4()), "content": "Doors open at 9", "message_type": "text", "mentions": []}, "Doors open at 9"))
    finally:
        emitters.emit_notification = original

    assert {u for u, _ in captured} == {p.mehta["id"], p.priya["id"], p.ravi["id"]}
    for _, payload in captured:
        assert payload["title"] == "event gccc" and payload["body"] == "Doors open at 9" and payload["type"] == "group_message"
        assert payload["preview"] == "Doors open at 9" and payload["category"] == "group_message" and payload["sender_name"] == "Dr Rao"


# ---- 1:1 chats -------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def direct(world):
    p = world.people
    conversation = Conversation(tenant_id=TENANT, status="open", conversation_type="direct", subject="Order enquiry", created_by=uid(p.priya))
    world.db.add(conversation)
    world.db.flush()
    for user, role in ((p.priya, "customer"), (p.rao, "provider")):
        world.db.add(ConversationParticipant(conversation_id=conversation.id, user_id=uid(user), role=role, membership_status="active"))
    world.db.add(DeviceToken(user_id=uid(p.priya), token="tok-priya", platform="ios", is_active=True))
    world.db.commit()
    return conversation


def send_direct(world, direct, user, **fields):
    world.notifications.clear(), world.pushes.clear()
    chat_service.send_message_service(world.db, user, MessageCreate(conversation_id=direct.id, **fields))
    [push] = push_of(world)
    return push, [n for n in world.notifications if n["category"] == "chat_message"]


def test_a_one_to_one_message_has_the_sender_name_and_preview(world, direct):
    push, feed = send_direct(world, direct, world.people.rao, content="Your order has shipped", message_type="text")

    assert push["title"] == "Dr Rao" and push["body"] == "Your order has shipped"
    data = push["data"]
    assert data["category"] == data["type"] == "chat_message" and data["conversation_id"] == str(direct.id) == data["conversationId"]
    assert data["preview"] == "Your order has shipped" and data["sender_name"] == "Dr Rao" and data["attachment_type"] == "none" and data["message_id"] == data["messageId"]
    assert all(isinstance(v, str) for v in data.values())
    [item] = feed
    assert item["title"] == "Dr Rao" and item["message"] == "Your order has shipped" and item["channels"] == ["in_app"]
    assert item["metadata"] == {k: v for k, v in data.items() if k not in ("conversationId", "messageId")}


def test_a_one_to_one_photo_says_sent_a_photo(world, direct):
    photo = ChatAttachment(conversation_id=direct.id, uploaded_by=uid(world.people.rao), file_name="a.jpg", file_path="x", mime_type="image/jpeg", file_size=1, attachment_type="image")
    world.db.add(photo)
    world.db.commit()

    push, feed = send_direct(world, direct, world.people.rao, message_type="image", attachment_id=photo.id)

    assert push["title"] == "Dr Rao" and push["body"] == "Sent a photo" and push["data"]["attachment_type"] == "image" and feed[0]["message"] == "Sent a photo"


def test_a_one_to_one_message_from_someone_with_no_known_name_falls_back_to_the_subject(world, direct):
    stranger = {"id": str(uuid4()), "role": "customer", "email": "x@example.test", "tenant_id": str(TENANT)}
    world.db.add(ConversationParticipant(conversation_id=direct.id, user_id=uid(stranger), role="customer", membership_status="active"))
    world.db.commit()

    push, _ = send_direct(world, direct, stranger, content="hello", message_type="text")

    assert push["title"] == "Order enquiry" and push["body"] == "hello" and "sender_name" not in push["data"]
