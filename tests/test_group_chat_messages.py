"""Group chat messages: sending, replies, @-tags, notifications, editing, retracting, hiding, pins, reactions, read
status, search and files.

Spec rules covered: nothing is permanently deleted (an edit keeps the original; a retract or an admin hide only hides);
edit within 15 minutes; the lock screen shows only "New message"; an @-tag notifies directly (and in a care group even
when muted); reactions are off in care groups and limited in program groups; a program group's patients do not see who
has read; search and files cover only what was sent after the person joined.
"""
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.models.chat_model import ChatAttachment, ChatNotification, ConversationParticipant, Message
from tests.group_chat_support import (
    accept, age_message, audit_actions, joined_group, make_group, messages_of, say, texts, uid, world,  # noqa: F401
)

API = "/api/v1/groups"


@pytest.fixture
def care(world):
    """Care group: Dr Rao (owner), Dr Mehta (provider), Priya (patient). Patients may post here."""
    p = world.people
    return joined_group(world, kind="care", members=[p.mehta, p.priya])


@pytest.fixture
def program(world):
    """Program group with posting open to everyone: Dr Rao (owner), Dr Mehta, patients Priya, Ravi, Asha."""
    p = world.people
    group = joined_group(world, kind="program", members=[p.mehta, p.priya, p.ravi, p.asha])
    world.as_user(p.rao).patch(f"{API}/{group['id']}/settings", json={"admins_only_posting": False})
    return group


def sent(world, user, group, text="hello", **extra):
    resp = say(world, user, group["id"], text, **extra)
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- sending --------------------------------------------------------------------------------------------------

def test_a_member_sends_and_everyone_else_sees_who_sent_it(world, care):
    p = world.people

    msg = sent(world, p.priya, care, "my knee feels better")

    assert msg["content"] == "my knee feels better" and msg["sender_id"] == p.priya["id"]
    assert msg["sender_name"] == "Priya Sharma" and msg["sender_kind"] == "patient" and msg["status"] == "visible"
    assert "my knee feels better" in texts(messages_of(world, p.rao, care["id"]))


def test_the_realtime_message_goes_to_the_groups_room_without_names(world, care):
    world.events.clear()

    msg = sent(world, world.people.priya, care, "hello team")

    [(_, cid, event, payload)] = [e for e in world.events if e[0] == "group" and e[2] == "new_message"]
    assert cid == care["id"] and str(payload["message"]["id"]) == msg["id"] and payload["message"]["content"] == "hello team"
    assert "sender_name" not in payload["message"]  # each reader gets names from their own member list (privacy per viewer)


@pytest.mark.parametrize("who", ["stranger", "unverified"])
def test_people_outside_the_group_cannot_send(world, care, who):
    assert say(world, getattr(world.people, who), care["id"], "hi").status_code == 403


def test_a_message_needs_text_or_an_attachment(world, care):
    assert say(world, world.people.rao, care["id"], "   ").status_code == 422
    assert world.as_user(world.people.rao).post(f"{API}/{care['id']}/messages", json={}).status_code == 422


def test_an_attachment_must_belong_to_this_group(world, care):
    p = world.people
    other = joined_group(world, kind="care", members=[p.priya], name="Another team")
    foreign = ChatAttachment(conversation_id=UUID(other["id"]), uploaded_by=uid(p.rao), file_name="scan.pdf", file_path="x/scan.pdf",
                             mime_type="application/pdf", file_size=10, attachment_type="document")
    own = ChatAttachment(conversation_id=UUID(care["id"]), uploaded_by=uid(p.rao), file_name="plan.pdf", file_path="y/plan.pdf",
                         mime_type="application/pdf", file_size=10, attachment_type="document")
    world.db.add_all([foreign, own])
    world.db.commit()

    bad = world.as_user(p.rao).post(f"{API}/{care['id']}/messages", json={"message_type": "document", "attachment_id": str(foreign.id)})
    good = world.as_user(p.rao).post(f"{API}/{care['id']}/messages", json={"message_type": "document", "attachment_id": str(own.id)})

    assert bad.status_code == 400 and good.status_code == 201 and good.json()["attachment_id"] == str(own.id)
    assert world.db.get(ChatAttachment, own.id).message_id == UUID(good.json()["id"])  # the file is tied to the message


def test_system_messages_cannot_be_sent_by_users(world, care):
    assert world.as_user(world.people.rao).post(f"{API}/{care['id']}/messages", json={"content": "x", "message_type": "system"}).status_code == 422


# --- replies --------------------------------------------------------------------------------------------------

def test_a_reply_quotes_the_message(world, care):
    p = world.people
    original = sent(world, p.rao, care, "please send your exercise log")

    reply = sent(world, p.priya, care, "done, sent it", reply_to_id=original["id"])

    assert reply["reply_to"] == {"id": original["id"], "sender_name": "Dr Rao", "preview": "please send your exercise log", "is_hidden": False}
    listed = [m for m in messages_of(world, p.mehta, care["id"])["items"] if m["id"] == reply["id"]][0]
    assert listed["reply_to"]["id"] == original["id"]


def test_replying_to_a_message_of_another_group_or_one_that_does_not_exist_is_refused(world, care):
    p = world.people
    other = joined_group(world, kind="care", members=[p.priya], name="Another team")
    foreign = sent(world, p.rao, other, "elsewhere")

    assert say(world, p.rao, care["id"], "x", reply_to_id=foreign["id"]).status_code == 404
    assert say(world, p.rao, care["id"], "x", reply_to_id=str(uuid4())).status_code == 404


def test_you_cannot_reply_to_something_sent_before_you_joined(world):
    p = world.people
    group = make_group(world, kind="care", invitees=[p.priya])
    early = sent(world, p.rao, group, "before")
    accept(world, p.priya, group["id"])

    assert say(world, p.priya, group["id"], "re", reply_to_id=early["id"]).status_code == 400


def test_a_quote_of_a_hidden_message_shows_no_text(world, care):
    p = world.people
    original = sent(world, p.priya, care, "something I regret")
    reply = sent(world, p.rao, care, "noted", reply_to_id=original["id"])

    world.as_user(p.priya).post(f"{API}/{care['id']}/messages/{original['id']}/retract")

    listed = [m for m in messages_of(world, p.mehta, care["id"])["items"] if m["id"] == reply["id"]][0]
    assert listed["reply_to"]["is_hidden"] is True and listed["reply_to"]["preview"] is None
    assert say(world, p.rao, care["id"], "again", reply_to_id=original["id"]).status_code == 400  # and cannot be quoted any more


# --- @-tags ------------------------------------------------------------------------------------------------------

def test_tagging_someone_marks_the_message_for_them(world, care):
    p = world.people

    msg = sent(world, p.rao, care, "@Priya please confirm", mentions=[p.priya["id"]])

    assert msg["mentions"] == [p.priya["id"]] and msg["mentions_me"] is False
    seen_by_priya = [m for m in messages_of(world, p.priya, care["id"])["items"] if m["id"] == msg["id"]][0]
    assert seen_by_priya["mentions_me"] is True
    seen_by_mehta = [m for m in messages_of(world, p.mehta, care["id"])["items"] if m["id"] == msg["id"]][0]
    assert seen_by_mehta["mentions_me"] is False


def test_only_people_in_the_group_can_be_tagged(world, care):
    assert say(world, world.people.rao, care["id"], "@x", mentions=[world.people.stranger["id"]]).status_code == 422


def test_providers_can_tag_everyone_patients_cannot(world, care):
    p = world.people

    assert say(world, p.rao, care["id"], "@everyone class is on", mention_all=True).status_code == 201
    refused = say(world, p.priya, care["id"], "@everyone", mention_all=True)
    assert refused.status_code == 403 and "Only providers can tag everyone" in refused.json()["detail"]
    everyone = [m for m in messages_of(world, p.priya, care["id"])["items"] if m["mentions_all"]][0]
    assert everyone["mentions_me"] is True  # everyone except the sender
    assert [m for m in messages_of(world, p.rao, care["id"])["items"] if m["mentions_all"]][0]["mentions_me"] is False


def test_a_tag_of_yourself_is_ignored(world, care):
    msg = sent(world, world.people.rao, care, "note to self", mentions=[world.people.rao["id"]])

    assert msg["mentions"] == []


# --- notifications: the lock screen shows only "New message" ----------------------------------------------------

def test_a_message_notification_has_no_name_no_group_and_no_text(world, care):
    p = world.people
    world.notifications.clear(), world.pushes.clear()

    sent(world, p.rao, care, "your blood test result is ready", )

    rows = world.db.query(ChatNotification).filter(ChatNotification.notification_type == "group_message").all()
    assert {r.user_id for r in rows} == {uid(p.mehta), uid(p.priya)}  # everyone but the sender
    for r in rows:
        assert r.title == "New message" and r.body == ""
        assert "blood" not in str(r.data) and set(r.data) == {"conversation_id", "message_id", "mentioned"}
    [feed] = [n for n in world.notifications if n["category"] == "group_message"]
    assert feed["title"] == "New message" and feed["message"] == "" and feed["channels"] == ["in_app"]
    assert "blood" not in str(feed) and "Yoga" not in str(feed) and "Dr Rao" not in str(feed)
    assert set(feed["metadata"]) == {"conversation_id", "message_id", "category"}


def test_push_for_a_group_message_is_generic_and_there_is_no_email_or_sms(world, care):
    from app.models.chat_model import DeviceToken

    p = world.people
    world.db.add(DeviceToken(user_id=uid(p.priya), token="tok-priya", platform="android", is_active=True))
    world.db.commit()
    world.pushes.clear()

    sent(world, p.rao, care, "private clinical detail")

    pushes = [x for x in world.pushes if "tokens" in x]
    assert pushes and pushes[0]["title"] == "New message" and pushes[0]["body"] == ""
    assert pushes[0]["data"]["type"] == "group_message" and pushes[0]["data"]["conversationId"] == care["id"]  # the app opens the Groups tab
    assert "private" not in str(pushes)
    assert not [x for x in world.pushes if x.get("email") or x.get("sms")]


def test_a_muted_member_gets_no_notification_but_the_message_is_there(world, care):
    p = world.people
    world.as_user(p.priya).patch(f"{API}/{care['id']}/preferences", json={"muted": True})
    world.notifications.clear()

    sent(world, p.rao, care, "ordinary message")

    [feed] = [n for n in world.notifications if n["category"] == "group_message"]
    assert feed["user_ids"] == [uid(p.mehta)]
    assert "ordinary message" in texts(messages_of(world, p.priya, care["id"]))
    assert world.as_user(p.priya).get(f"{API}/{care['id']}").json()["unread_count"] >= 1  # still counted as unread


def test_in_a_care_group_a_tag_reaches_a_member_who_muted_the_group(world, care):
    p = world.people
    world.as_user(p.priya).patch(f"{API}/{care['id']}/preferences", json={"muted": True})
    world.notifications.clear()

    sent(world, p.rao, care, "@Priya a quick question", mentions=[p.priya["id"]])

    [feed] = [n for n in world.notifications if n["category"] == "group_message"]
    assert uid(p.priya) in feed["user_ids"]
    row = world.db.query(ChatNotification).filter_by(user_id=uid(p.priya)).order_by(ChatNotification.created_at.desc()).first()
    assert row.data["mentioned"] is True and row.body == ""  # even a tag shows no message text


def test_in_a_program_group_a_mute_is_always_respected_even_for_a_tag(world, program):
    p = world.people
    world.as_user(p.priya).patch(f"{API}/{program['id']}/preferences", json={"muted": True})
    world.notifications.clear()

    sent(world, p.rao, program, "@Priya", mentions=[p.priya["id"]])

    [feed] = [n for n in world.notifications if n["category"] == "group_message"]
    assert uid(p.priya) not in feed["user_ids"]


def test_a_mute_that_has_ended_notifies_again(world, care):
    p = world.people
    world.as_user(p.priya).patch(f"{API}/{care['id']}/preferences", json={"muted": True, "muted_until": (datetime.utcnow() + timedelta(hours=1)).isoformat()})
    row = world.db.query(ConversationParticipant).filter_by(user_id=uid(p.priya)).one()
    row.muted_until = datetime.utcnow() - timedelta(minutes=1)
    world.db.commit()
    world.notifications.clear()

    sent(world, p.rao, care, "x")

    assert uid(p.priya) in [n for n in world.notifications if n["category"] == "group_message"][0]["user_ids"]


def test_the_realtime_notification_event_is_generic_too(world, care):
    import asyncio

    from app.realtime import emitters

    p = world.people
    captured = []

    async def fake_emit(user_id, payload):
        captured.append((user_id, payload))

    original = emitters.emit_notification
    emitters.emit_notification = fake_emit
    try:
        asyncio.run(emitters.notify_participants_new_message(world.db, UUID(care["id"]), p.rao["id"], {"content": "secret", "mentions": []}, "secret preview"))
    finally:
        emitters.emit_notification = original

    assert {u for u, _ in captured} == {p.mehta["id"], p.priya["id"]}
    for _, payload in captured:
        assert payload == {"type": "group_message", "conversation_id": care["id"], "title": "New message", "body": ""}


def test_the_realtime_notification_skips_people_who_are_not_active_members(world):
    import asyncio

    from app.realtime import emitters

    p = world.people
    group = make_group(world, kind="care", invitees=[p.priya])  # invited, not accepted
    captured = []

    async def fake_emit(user_id, payload):
        captured.append(user_id)

    original = emitters.emit_notification
    emitters.emit_notification = fake_emit
    try:
        asyncio.run(emitters.notify_participants_new_message(world.db, UUID(group["id"]), p.rao["id"], {}, "x"))
    finally:
        emitters.emit_notification = original

    assert captured == []  # Priya has not agreed to be here, so she is told nothing about messages


# --- reading ------------------------------------------------------------------------------------------------------------

def test_messages_come_oldest_to_newest_with_a_cursor_for_older_ones(world, care):
    p = world.people
    for i in range(7):
        sent(world, p.rao, care, f"m{i}")

    first = messages_of(world, p.priya, care["id"], limit=4)
    older = messages_of(world, p.priya, care["id"], limit=4, cursor=first["next_cursor"])

    assert texts(first)[-3:] == ["m4", "m5", "m6"] and first["has_more"] is True
    assert texts(older)[-1] == "m3" and older["has_more"] is True or older["has_more"] is False
    everything = texts(older) + texts(first)
    assert [t for t in everything if t.startswith("m")] == [f"m{i}" for i in range(7)]


def test_unread_counts_and_the_new_messages_marker(world, care):
    p = world.people
    sent(world, p.rao, care, "one")
    sent(world, p.rao, care, "two")

    body = messages_of(world, p.priya, care["id"])
    first_unread = [m for m in body["items"] if m["content"] == "one"][0]["id"]

    assert body["unread_count"] == 2 and body["first_unread_message_id"] == first_unread
    assert world.as_user(p.priya).get(f"{API}/{care['id']}").json()["unread_count"] == 2
    assert world.as_user(p.priya).get(API).json()["items"][0]["unread_count"] == 2

    assert world.as_user(p.priya).post(f"{API}/{care['id']}/read").status_code == 200
    after = messages_of(world, p.priya, care["id"])
    assert after["unread_count"] == 0 and after["first_unread_message_id"] is None

    sent(world, p.rao, care, "three")
    again = messages_of(world, p.priya, care["id"])
    assert again["unread_count"] == 1 and [m for m in again["items"] if m["id"] == again["first_unread_message_id"]][0]["content"] == "three"


def test_your_own_messages_and_system_lines_are_not_unread(world, care):
    p = world.people
    sent(world, p.priya, care, "mine")

    assert world.as_user(p.priya).get(f"{API}/{care['id']}").json()["unread_count"] == 0


def test_the_group_list_shows_the_last_visible_message(world, care):
    p = world.people
    sent(world, p.rao, care, "first")
    last = sent(world, p.rao, care, "the latest")

    assert world.as_user(p.priya).get(API).json()["items"][0]["last_message_preview"] == "the latest"
    world.as_user(p.rao).post(f"{API}/{care['id']}/messages/{last['id']}/retract")
    assert world.as_user(p.priya).get(API).json()["items"][0]["last_message_preview"] == "first"  # a hidden message never leaks via the preview


# --- editing: 15 minutes, the original is kept ----------------------------------------------------------------------------

def test_a_message_can_be_edited_within_15_minutes_and_shows_edited(world, care):
    p = world.people
    msg = sent(world, p.priya, care, "I feel pain in my left knee")

    resp = world.as_user(p.priya).patch(f"{API}/{care['id']}/messages/{msg['id']}", json={"content": "I feel pain in my right knee"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == "I feel pain in my right knee" and body["is_edited"] is True and body["edited_at"]
    assert "I feel pain in my right knee" in texts(messages_of(world, p.rao, care["id"]))
    assert "message_edited" in audit_actions(world, care["id"])
    assert world.db.query(Message).filter_by(id=UUID(msg["id"])).one().edit_history[0]["content"] == "I feel pain in my left knee"


def test_every_earlier_version_is_kept(world, care):
    p = world.people
    msg = sent(world, p.priya, care, "v1")
    for text in ("v2", "v3"):
        world.as_user(p.priya).patch(f"{API}/{care['id']}/messages/{msg['id']}", json={"content": text})

    original = world.as_user(p.rao).get(f"{API}/{care['id']}/messages/{msg['id']}/original").json()

    assert original["content"] == "v1" and original["current_content"] == "v3"
    assert [v["content"] for v in original["versions"]] == ["v1", "v2"]


def test_after_15_minutes_it_can_no_longer_be_edited(world, care):
    p = world.people
    msg = sent(world, p.priya, care, "old news")
    age_message(world, msg["id"], 16)

    resp = world.as_user(p.priya).patch(f"{API}/{care['id']}/messages/{msg['id']}", json={"content": "changed my mind"})

    assert resp.status_code == 403 and "15 minutes" in resp.json()["detail"]
    age_message(world, msg["id"], 14)
    assert world.as_user(p.priya).patch(f"{API}/{care['id']}/messages/{msg['id']}", json={"content": "just in time"}).status_code == 200


def test_the_edit_window_is_a_setting(world, care, monkeypatch):
    from app.core.config import settings

    msg = sent(world, world.people.priya, care, "x")
    age_message(world, msg["id"], 5)
    monkeypatch.setattr(settings, "GROUP_EDIT_WINDOW_MINUTES", 3)

    assert world.as_user(world.people.priya).patch(f"{API}/{care['id']}/messages/{msg['id']}", json={"content": "y"}).status_code == 403


def test_only_the_author_edits_even_an_admin_cannot_edit_someone_elses(world, care):
    p = world.people
    msg = sent(world, p.priya, care, "hers")

    assert world.as_user(p.rao).patch(f"{API}/{care['id']}/messages/{msg['id']}", json={"content": "mine now"}).status_code == 403
    assert world.as_user(p.stranger).patch(f"{API}/{care['id']}/messages/{msg['id']}", json={"content": "x"}).status_code == 403


def test_an_edit_that_changes_nothing_is_a_no_op(world, care):
    p = world.people
    msg = sent(world, p.priya, care, "same")

    body = world.as_user(p.priya).patch(f"{API}/{care['id']}/messages/{msg['id']}", json={"content": "same"}).json()

    assert body["is_edited"] is False
    assert world.db.query(Message).filter_by(id=UUID(msg["id"])).one().edit_history is None


def test_a_retracted_message_and_a_file_message_cannot_be_edited(world, care):
    p = world.people
    gone = sent(world, p.priya, care, "retracted soon")
    world.as_user(p.priya).post(f"{API}/{care['id']}/messages/{gone['id']}/retract")
    attachment = ChatAttachment(conversation_id=UUID(care["id"]), uploaded_by=uid(p.priya), file_name="a.png", file_path="a/a.png",
                                mime_type="image/png", file_size=1, attachment_type="image")
    world.db.add(attachment)
    world.db.commit()
    photo = world.as_user(p.priya).post(f"{API}/{care['id']}/messages", json={"message_type": "image", "attachment_id": str(attachment.id)}).json()

    assert world.as_user(p.priya).patch(f"{API}/{care['id']}/messages/{gone['id']}", json={"content": "x"}).status_code == 409
    assert world.as_user(p.priya).patch(f"{API}/{care['id']}/messages/{photo['id']}", json={"content": "x"}).status_code == 400


def test_the_list_preview_follows_an_edit_of_the_latest_message(world, care):
    p = world.people
    msg = sent(world, p.rao, care, "see you at 5")

    world.as_user(p.rao).patch(f"{API}/{care['id']}/messages/{msg['id']}", json={"content": "see you at 6"})

    assert world.as_user(p.priya).get(API).json()["items"][0]["last_message_preview"] == "see you at 6"


# --- retract: hidden from the group, the original stays ------------------------------------------------------------------------

def test_retracting_hides_the_message_from_everyone_and_keeps_the_original(world, care):
    p = world.people
    msg = sent(world, p.priya, care, "I should not have written this")

    resp = world.as_user(p.priya).post(f"{API}/{care['id']}/messages/{msg['id']}/retract")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "retracted" and body["placeholder"] == "Message retracted" and body["content"] is None
    for viewer in (p.rao, p.mehta, p.priya):
        shown = [m for m in messages_of(world, viewer, care["id"])["items"] if m["id"] == msg["id"]][0]
        assert shown["content"] is None and shown["placeholder"] == "Message retracted"
    # nothing was deleted: the original is in the record, readable by the owner
    original = world.as_user(p.rao).get(f"{API}/{care['id']}/messages/{msg['id']}/original").json()
    assert original["content"] == "I should not have written this" and original["status"] == "retracted"
    assert world.db.query(Message).filter_by(id=UUID(msg["id"])).one().is_deleted is False
    assert "message_retracted" in audit_actions(world, care["id"])
    assert ("group", care["id"], "message_hidden", {"message_id": msg["id"], "status": "retracted", "placeholder": "Message retracted"}) in world.events


def test_only_the_author_can_retract_and_system_lines_cannot_be(world, care):
    p = world.people
    msg = sent(world, p.priya, care, "mine")
    system_line = [m for m in messages_of(world, p.rao, care["id"])["items"] if m["message_type"] == "system"][0]

    assert world.as_user(p.rao).post(f"{API}/{care['id']}/messages/{msg['id']}/retract").status_code == 403
    assert world.as_user(p.rao).post(f"{API}/{care['id']}/messages/{system_line['id']}/retract").status_code == 400


def test_retracting_twice_is_the_same_answer(world, care):
    p = world.people
    msg = sent(world, p.priya, care, "x")

    first = world.as_user(p.priya).post(f"{API}/{care['id']}/messages/{msg['id']}/retract")
    second = world.as_user(p.priya).post(f"{API}/{care['id']}/messages/{msg['id']}/retract")

    assert first.status_code == second.status_code == 200 and second.json()["status"] == "retracted"
    assert audit_actions(world, care["id"]).count("message_retracted") == 1


def test_a_retract_is_a_normal_option_after_the_edit_window(world, care):
    p = world.people
    msg = sent(world, p.priya, care, "old")
    age_message(world, msg["id"], 120)

    assert world.as_user(p.priya).post(f"{API}/{care['id']}/messages/{msg['id']}/retract").status_code == 200


# --- admin hide ------------------------------------------------------------------------------------------------------------

def test_an_admin_can_remove_any_message_and_a_note_stays(world, care):
    p = world.people
    msg = sent(world, p.priya, care, "this breaks the rules")

    resp = world.as_user(p.rao).post(f"{API}/{care['id']}/messages/{msg['id']}/hide", json={"reason": "shared a phone number"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "removed_by_admin" and body["placeholder"] == "Message removed by admin" and body["content"] is None
    shown = [m for m in messages_of(world, p.priya, care["id"])["items"] if m["id"] == msg["id"]][0]
    assert shown["placeholder"] == "Message removed by admin"
    original = world.as_user(p.rao).get(f"{API}/{care['id']}/messages/{msg['id']}/original").json()
    assert original["content"] == "this breaks the rules" and original["hidden_reason"] == "shared a phone number"
    assert original["hidden_by"] == p.rao["id"]
    entry = [e for e in world.as_user(p.rao).get(f"{API}/{care['id']}/audit").json()["items"] if e["action"] == "message_hidden"][0]
    assert entry["reason"] == "shared a phone number" and entry["target_user_id"] == p.priya["id"]


def test_hiding_needs_an_admin_and_a_reason(world, care):
    p = world.people
    msg = sent(world, p.rao, care, "x")

    assert world.as_user(p.priya).post(f"{API}/{care['id']}/messages/{msg['id']}/hide", json={"reason": "why not"}).status_code == 403
    assert world.as_user(p.mehta).post(f"{API}/{care['id']}/messages/{msg['id']}/hide", json={"reason": "why not"}).status_code == 403  # a member provider is not an admin
    assert world.as_user(p.rao).post(f"{API}/{care['id']}/messages/{msg['id']}/hide", json={}).status_code == 422


def test_an_admin_provider_can_hide_too_and_system_lines_cannot_be_hidden(world, care):
    p = world.people
    world.as_user(p.rao).patch(f"{API}/{care['id']}/members/{p.mehta['id']}/role", json={"role": "admin"})
    msg = sent(world, p.priya, care, "x")
    system_line = [m for m in messages_of(world, p.rao, care["id"])["items"] if m["message_type"] == "system"][0]

    assert world.as_user(p.mehta).post(f"{API}/{care['id']}/messages/{msg['id']}/hide", json={"reason": "moderation"}).status_code == 200
    assert world.as_user(p.rao).post(f"{API}/{care['id']}/messages/{system_line['id']}/hide", json={"reason": "moderation"}).status_code == 400


def test_a_hidden_message_cannot_be_hidden_a_retract_after_a_hide_changes_nothing(world, care):
    p = world.people
    msg = sent(world, p.priya, care, "x")
    world.as_user(p.rao).post(f"{API}/{care['id']}/messages/{msg['id']}/hide", json={"reason": "moderation"})

    again = world.as_user(p.priya).post(f"{API}/{care['id']}/messages/{msg['id']}/retract")

    assert again.json()["status"] == "removed_by_admin"  # still shows it was removed by an admin


# --- the secure record ---------------------------------------------------------------------------------------------------------

def test_the_original_is_only_for_the_owner_and_compliance_and_every_read_is_audited(world, care):
    p = world.people
    msg = sent(world, p.priya, care, "kept in the record")
    world.as_user(p.priya).post(f"{API}/{care['id']}/messages/{msg['id']}/retract")
    url = f"{API}/{care['id']}/messages/{msg['id']}/original"

    assert world.as_user(p.priya).get(url).status_code == 403  # even the author
    assert world.as_user(p.mehta).get(url).status_code == 403
    assert world.as_user(p.outsider_admin).get(url).status_code == 403
    assert world.as_user(p.stranger).get(url).status_code == 403
    for reader in (p.rao, p.admin, p.root):
        assert world.as_user(reader).get(url).json()["content"] == "kept in the record"
    assert audit_actions(world, care["id"]).count("original_viewed") == 3


# --- pins --------------------------------------------------------------------------------------------------------------------------

def test_providers_can_pin_and_unpin_messages(world, care):
    p = world.people
    msg = sent(world, p.rao, care, "clinic hours: 9 to 5")

    pinned = world.as_user(p.mehta).put(f"{API}/{care['id']}/messages/{msg['id']}/pin")

    assert pinned.status_code == 200 and pinned.json()["pinned"] is True
    assert [m["content"] for m in world.as_user(p.priya).get(f"{API}/{care['id']}/pinned").json()["items"]] == ["clinic hours: 9 to 5"]
    assert messages_of(world, p.priya, care["id"])["pinned_count"] == 1
    assert world.as_user(p.rao).delete(f"{API}/{care['id']}/messages/{msg['id']}/pin").json()["pinned"] is False
    assert world.as_user(p.priya).get(f"{API}/{care['id']}/pinned").json()["items"] == []
    assert {"message_pinned", "message_unpinned"} <= set(audit_actions(world, care["id"]))


def test_patients_cannot_pin(world, care):
    p = world.people
    msg = sent(world, p.rao, care, "x")

    assert world.as_user(p.priya).put(f"{API}/{care['id']}/messages/{msg['id']}/pin").status_code == 403


def test_hiding_a_pinned_message_unpins_it(world, care):
    p = world.people
    msg = sent(world, p.rao, care, "pinned then removed")
    world.as_user(p.rao).put(f"{API}/{care['id']}/messages/{msg['id']}/pin")

    world.as_user(p.rao).post(f"{API}/{care['id']}/messages/{msg['id']}/hide", json={"reason": "wrong info"})

    assert world.as_user(p.priya).get(f"{API}/{care['id']}/pinned").json()["items"] == []


def test_a_system_line_and_a_hidden_message_cannot_be_pinned(world, care):
    p = world.people
    system_line = [m for m in messages_of(world, p.rao, care["id"])["items"] if m["message_type"] == "system"][0]

    assert world.as_user(p.rao).put(f"{API}/{care['id']}/messages/{system_line['id']}/pin").status_code == 409


# --- reactions ---------------------------------------------------------------------------------------------------------------------

def test_reactions_are_off_in_a_care_group(world, care):
    p = world.people
    msg = sent(world, p.rao, care, "x")

    assert world.as_user(p.priya).put(f"{API}/{care['id']}/messages/{msg['id']}/reactions", json={"emoji": "\U0001F44D"}).status_code == 403
    assert world.as_user(p.priya).get(f"{API}/{care['id']}/reactions").json() == {"enabled": False, "emojis": []}


def test_a_program_group_allows_a_small_set(world, program):
    p = world.people
    msg = sent(world, p.rao, program, "see you tomorrow")
    thumbs, heart = "\U0001F44D", "❤️"

    assert world.as_user(p.priya).put(f"{API}/{program['id']}/messages/{msg['id']}/reactions", json={"emoji": thumbs}).json()["reactions"] == {thumbs: 1}
    world.as_user(p.ravi).put(f"{API}/{program['id']}/messages/{msg['id']}/reactions", json={"emoji": thumbs})
    final = world.as_user(p.ravi).put(f"{API}/{program['id']}/messages/{msg['id']}/reactions", json={"emoji": heart}).json()

    assert final["reactions"] == {thumbs: 2, heart: 1} and sorted(final["my_reactions"]) == sorted([thumbs, heart])
    listed = [m for m in messages_of(world, p.asha, program["id"])["items"] if m["id"] == msg["id"]][0]
    assert listed["reactions"] == {thumbs: 2, heart: 1} and listed["my_reactions"] == []
    assert world.as_user(p.asha).get(f"{API}/{program['id']}/reactions").json()["enabled"] is True


def test_other_emojis_are_refused_and_reacting_twice_counts_once(world, program):
    p = world.people
    msg = sent(world, p.rao, program, "x")
    url = f"{API}/{program['id']}/messages/{msg['id']}/reactions"

    assert world.as_user(p.priya).put(url, json={"emoji": "\U0001F4A9"}).status_code == 422
    world.as_user(p.priya).put(url, json={"emoji": "\U0001F44D"})
    again = world.as_user(p.priya).put(url, json={"emoji": "\U0001F44D"}).json()
    assert again["reactions"] == {"\U0001F44D": 1}


def test_a_reaction_can_be_taken_back(world, program):
    p = world.people
    msg = sent(world, p.rao, program, "x")
    url = f"{API}/{program['id']}/messages/{msg['id']}/reactions"
    world.as_user(p.priya).put(url, json={"emoji": "\U0001F44D"})

    gone = world.as_user(p.priya).delete(url, params={"emoji": "\U0001F44D"}).json()

    assert gone["reactions"] == {} and gone["my_reactions"] == []


def test_you_cannot_react_to_a_removed_message(world, program):
    p = world.people
    msg = sent(world, p.priya, program, "x")
    world.as_user(p.rao).post(f"{API}/{program['id']}/messages/{msg['id']}/hide", json={"reason": "moderation"})

    assert world.as_user(p.ravi).put(f"{API}/{program['id']}/messages/{msg['id']}/reactions", json={"emoji": "\U0001F44D"}).status_code == 409


# --- who has read it -------------------------------------------------------------------------------------------------------------------

def test_in_a_care_group_everyone_sees_seen_by_n_of_m(world):
    p = world.people
    group = joined_group(world, kind="care", members=[p.mehta, p.priya])
    msg = sent(world, p.rao, group, "team update")
    world.as_user(p.mehta).post(f"{API}/{group['id']}/read")

    status_for_priya = world.as_user(p.priya).get(f"{API}/{group['id']}/messages/{msg['id']}/read-status")

    assert status_for_priya.status_code == 200
    body = status_for_priya.json()
    assert body["label"] == "seen by 1 of 2" and body["seen_by"] == [p.mehta["id"]] and body["recipient_count"] == 2
    listed = [m for m in messages_of(world, p.rao, group["id"])["items"] if m["id"] == msg["id"]][0]
    assert listed["read_by"] == [p.mehta["id"]]
    world.as_user(p.priya).post(f"{API}/{group['id']}/read")
    assert world.as_user(p.rao).get(f"{API}/{group['id']}/messages/{msg['id']}/read-status").json()["label"] == "seen by 2 of 2"


def test_in_a_program_group_patients_do_not_see_who_has_read_but_providers_do(world, program):
    p = world.people
    msg = sent(world, p.rao, program, "class notes")
    world.as_user(p.ravi).post(f"{API}/{program['id']}/read")

    assert world.as_user(p.priya).get(f"{API}/{program['id']}/messages/{msg['id']}/read-status").status_code == 403
    provider_view = world.as_user(p.mehta).get(f"{API}/{program['id']}/messages/{msg['id']}/read-status")
    assert provider_view.status_code == 200 and provider_view.json()["seen_by_count"] == 1
    assert all(m["read_by"] is None for m in messages_of(world, p.priya, program["id"])["items"])  # no read_by anywhere in the list


def test_read_status_counts_only_people_who_were_there_when_it_was_sent(world):
    p = world.people
    group = make_group(world, kind="care", invitees=[p.priya])
    msg = sent(world, p.rao, group, "before priya")
    accept(world, p.priya, group["id"])

    assert world.as_user(p.rao).get(f"{API}/{group['id']}/messages/{msg['id']}/read-status").json()["recipient_count"] == 0


# --- search and files: only what was sent after joining ------------------------------------------------------------------------

def test_search_covers_only_messages_after_joining_and_skips_removed_ones(world):
    p = world.people
    group = make_group(world, kind="care", invitees=[p.priya])
    sent(world, p.rao, group, "appointment moved earlier")
    accept(world, p.priya, group["id"])
    sent(world, p.rao, group, "appointment confirmed")
    hidden = sent(world, p.rao, group, "appointment secret")
    world.as_user(p.rao).post(f"{API}/{group['id']}/messages/{hidden['id']}/hide", json={"reason": "wrong"})

    found = world.as_user(p.priya).get(f"{API}/{group['id']}/search", params={"q": "appointment"}).json()
    owner_found = world.as_user(p.rao).get(f"{API}/{group['id']}/search", params={"q": "appointment"}).json()

    assert [m["content"] for m in found["items"]] == ["appointment confirmed"]
    assert sorted(m["content"] for m in owner_found["items"]) == ["appointment confirmed", "appointment moved earlier"]


def test_search_needs_two_characters_and_membership(world, care):
    assert world.as_user(world.people.priya).get(f"{API}/{care['id']}/search", params={"q": "a"}).status_code == 422
    assert world.as_user(world.people.stranger).get(f"{API}/{care['id']}/search", params={"q": "hello"}).status_code == 403


def test_shared_files_cover_only_what_was_sent_after_joining(world):
    p = world.people
    group = make_group(world, kind="care", invitees=[p.priya])

    def attach(name):
        a = ChatAttachment(conversation_id=UUID(group["id"]), uploaded_by=uid(p.rao), file_name=name, file_path=f"f/{name}",
                           mime_type="application/pdf", file_size=1, attachment_type="document")
        world.db.add(a)
        world.db.commit()
        return world.as_user(p.rao).post(f"{API}/{group['id']}/messages", json={"message_type": "document", "attachment_id": str(a.id)}).json()

    attach("before.pdf")
    accept(world, p.priya, group["id"])
    after = attach("after.pdf")
    removed = attach("removed.pdf")
    world.as_user(p.rao).post(f"{API}/{group['id']}/messages/{removed['id']}/hide", json={"reason": "wrong file"})

    assert [f["file_name"] for f in world.as_user(p.priya).get(f"{API}/{group['id']}/files").json()["items"]] == ["after.pdf"]
    assert sorted(f["file_name"] for f in world.as_user(p.rao).get(f"{API}/{group['id']}/files").json()["items"]) == ["after.pdf", "before.pdf"]
    assert world.as_user(p.priya).get(f"{API}/{group['id']}/files").json()["items"][0]["message_id"] == after["id"]


# --- saving to the clinical record -------------------------------------------------------------------------------------------------

def test_a_provider_saves_a_message_to_a_patients_record_as_a_snapshot(world, care):
    p = world.people
    msg = sent(world, p.priya, care, "my pain is 4 out of 10")

    resp = world.as_user(p.mehta).post(f"{API}/{care['id']}/messages/{msg['id']}/save-to-record", json={"patient_user_id": p.priya["id"], "note": "pain score"})

    assert resp.status_code == 201
    saved = resp.json()
    assert saved["content_snapshot"] == "my pain is 4 out of 10" and saved["patient_user_id"] == p.priya["id"] and saved["saved_by"] == p.mehta["id"]
    # the message may later change; the record keeps what was saved
    world.as_user(p.priya).patch(f"{API}/{care['id']}/messages/{msg['id']}", json={"content": "my pain is 2 out of 10"})
    world.as_user(p.priya).post(f"{API}/{care['id']}/messages/{msg['id']}/retract")
    entries = world.as_user(p.mehta).get(f"{API}/{care['id']}/clinical-records").json()
    assert [e["content_snapshot"] for e in entries] == ["my pain is 4 out of 10"]
    assert "clinical_record_saved" in audit_actions(world, care["id"])


def test_only_providers_save_to_a_record_and_only_for_a_patient_in_the_group(world, care):
    p = world.people
    msg = sent(world, p.priya, care, "x")

    assert world.as_user(p.priya).post(f"{API}/{care['id']}/messages/{msg['id']}/save-to-record", json={"patient_user_id": p.priya["id"]}).status_code == 403
    assert world.as_user(p.mehta).post(f"{API}/{care['id']}/messages/{msg['id']}/save-to-record", json={"patient_user_id": p.stranger["id"]}).status_code == 422
    assert world.as_user(p.mehta).post(f"{API}/{care['id']}/messages/{msg['id']}/save-to-record", json={"patient_user_id": p.rao["id"]}).status_code == 422  # a provider is not a patient


def test_saving_twice_is_refused_and_hidden_messages_cannot_be_saved(world, care):
    p = world.people
    msg = sent(world, p.priya, care, "x")
    body = {"patient_user_id": p.priya["id"]}
    assert world.as_user(p.mehta).post(f"{API}/{care['id']}/messages/{msg['id']}/save-to-record", json=body).status_code == 201
    assert world.as_user(p.mehta).post(f"{API}/{care['id']}/messages/{msg['id']}/save-to-record", json=body).status_code == 409
    gone = sent(world, p.priya, care, "retracted")
    world.as_user(p.priya).post(f"{API}/{care['id']}/messages/{gone['id']}/retract")
    assert world.as_user(p.mehta).post(f"{API}/{care['id']}/messages/{gone['id']}/save-to-record", json=body).status_code == 409


def test_who_can_read_the_clinical_record_entries(world, care):
    p = world.people

    assert world.as_user(p.mehta).get(f"{API}/{care['id']}/clinical-records").status_code == 200
    assert world.as_user(p.admin).get(f"{API}/{care['id']}/clinical-records").status_code == 200
    assert world.as_user(p.priya).get(f"{API}/{care['id']}/clinical-records").status_code == 403
    assert world.as_user(p.outsider_admin).get(f"{API}/{care['id']}/clinical-records").status_code == 403


# --- the old, generic chat endpoints cannot be used to get around any of this ----------------------------------------------------------

def test_the_generic_endpoints_refuse_a_groups_messages(world, care):
    p = world.people
    msg = sent(world, p.priya, care, "private")
    cid = care["id"]

    assert world.as_user(p.rao).get(f"/api/v1/conversations/{cid}/messages").status_code == 409
    assert world.as_user(p.priya).patch(f"/api/v1/messages/{msg['id']}", json={"content": "x"}).status_code == 409
    assert world.as_user(p.rao).delete(f"/api/v1/messages/{msg['id']}").status_code == 409
    assert world.as_user(p.rao).get(f"/api/v1/messages/{msg['id']}/read-status").status_code == 409


def test_a_hidden_message_cannot_be_read_through_the_generic_message_search(world, care):
    p = world.people
    msg = sent(world, p.priya, care, "findable secret")
    world.as_user(p.priya).post(f"{API}/{care['id']}/messages/{msg['id']}/retract")

    for viewer in (p.rao, p.priya):
        found = world.as_user(viewer).get("/api/v1/messages/search", params={"q": "findable"})
        assert found.status_code == 200 and found.json()["items"] == []


def test_the_generic_send_path_obeys_the_group_rules(world, program):
    """POST /messages and the socket send_message event both go through here."""
    p = world.people
    world.as_user(p.rao).patch(f"{API}/{program['id']}/settings", json={"admins_only_posting": True})

    patient = world.as_user(p.priya).post("/api/v1/messages/", json={"conversation_id": program["id"], "content": "hi"})
    provider = world.as_user(p.mehta).post("/api/v1/messages/", json={"conversation_id": program["id"], "content": "notice"})

    assert patient.status_code == 403 and provider.status_code == 201
    assert "notice" in texts(messages_of(world, p.priya, program["id"]))


def test_a_person_who_is_not_a_member_cannot_send_through_the_generic_path(world, program):
    assert world.as_user(world.people.stranger).post("/api/v1/messages/", json={"conversation_id": program["id"], "content": "hi"}).status_code == 403


def test_typing_indicators_exist_only_in_care_groups(world, care, program):
    p = world.people

    assert world.as_user(p.priya).put(f"/api/v1/conversations/{care['id']}/typing", json={"is_typing": True}).status_code == 200
    assert world.as_user(p.priya).put(f"/api/v1/conversations/{program['id']}/typing", json={"is_typing": True}).status_code == 409
