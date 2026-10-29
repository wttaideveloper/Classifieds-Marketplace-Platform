"""One inbox: GET /conversations/ lists 1:1 chats and accepted groups in the same shape, newest activity first."""
from datetime import datetime, timedelta
from uuid import UUID

import pytest

from app.models.chat_model import Conversation, ConversationParticipant, Message
from tests.group_chat_support import accept, joined_group, make_group, say, uid, world  # noqa: F401

INBOX = "/api/v1/conversations/"
API = "/api/v1/groups"

ITEM_FIELDS = {"id", "status", "conversation_type", "subject", "last_message_at", "last_message_preview", "last_message", "unread_count", "assigned_provider_id",
               "other_participant_user_id", "is_archived", "archived_at", "updated_at"}


def direct_chat(world, starter, other, text=None):
    chat = world.as_user(starter).post(INBOX, json={"participant_ids": [{"user_id": other["id"], "role": "provider"}]}).json()
    if text:
        assert world.as_user(starter).post("/api/v1/messages/", json={"conversation_id": chat["id"], "content": text}).status_code == 201
    return chat


def inbox(world, user, **params):
    resp = world.as_user(user).get(INBOX, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def stamp(world, conversation_id, when):
    """Make the conversation's latest message (and the conversation) that old, to control the order."""
    cid = UUID(conversation_id)
    conversation = world.db.get(Conversation, cid)
    conversation.last_message_at = conversation.updated_at = when
    message = world.db.query(Message).filter_by(conversation_id=cid).order_by(Message.created_at.desc()).first()
    if message is not None:
        message.created_at = when
    world.db.commit()


def test_a_patient_sees_chats_and_groups_in_one_list_in_the_same_shape(world):
    p = world.people
    chat = direct_chat(world, p.priya, p.rao, "hello doctor")
    group = joined_group(world, kind="care", members=[p.mehta, p.priya])
    assert say(world, p.mehta, group["id"], "ward round at 9").status_code == 201

    body = inbox(world, p.priya)

    assert {i["id"]: i["conversation_type"] for i in body["items"]} == {chat["id"]: "direct", group["id"]: "group"}
    assert body["pagination"]["total"] == 2
    for item in body["items"]:
        assert ITEM_FIELDS <= set(item)
    by_id = {i["id"]: i for i in body["items"]}
    assert by_id[chat["id"]]["last_message"]["sender_id"] == p.priya["id"] and by_id[chat["id"]]["last_message"]["sender_name"] == "Priya Sharma"
    assert by_id[chat["id"]]["last_message"]["preview"] == "hello doctor" == by_id[chat["id"]]["last_message_preview"]
    assert by_id[chat["id"]]["last_message"]["read_by"] == [] and by_id[chat["id"]]["member_count"] is None and by_id[chat["id"]]["can_post"] is None


def test_a_group_row_has_the_senders_name_unread_count_and_group_fields(world):
    p = world.people
    group = joined_group(world, kind="care", members=[p.mehta, p.priya])
    assert say(world, p.mehta, group["id"], "ward round at 9").status_code == 201
    assert say(world, p.mehta, group["id"], "bring the report").status_code == 201

    row = next(i for i in inbox(world, p.priya)["items"] if i["id"] == group["id"])

    assert row["conversation_type"] == "group" and row["group_kind"] == "care" and row["subject"] == group["name"] and row["status"] == "open"
    assert row["last_message"] == {"sender_id": p.mehta["id"], "sender_name": "Dr Mehta", "preview": "bring the report", "read_by": None}
    assert row["last_message_preview"] == "bring the report" and row["last_message_at"]
    assert row["unread_count"] == 2 and row["member_count"] == 3 and row["can_post"] is True and row["visibility_notice"]
    assert row["assigned_provider_id"] is None and row["other_participant_user_id"] is None and row["is_archived"] is False and row["archived_at"] is None
    world.as_user(p.priya).post(f"{API}/{group['id']}/read")
    assert next(i for i in inbox(world, p.priya)["items"] if i["id"] == group["id"])["unread_count"] == 0
    assert row["unread_count"] == 2                                                                                   # own messages never count: checked next
    own = say(world, p.priya, group["id"], "on my way")
    assert own.status_code == 201 and next(i for i in inbox(world, p.priya)["items"] if i["id"] == group["id"])["unread_count"] == 0


def test_the_senders_name_follows_the_privacy_rules_of_the_group(world):
    p = world.people
    program = joined_group(world, kind="program", members=[p.priya, p.ravi])
    world.as_user(p.rao).patch(f"{API}/{program['id']}/settings", json={"admins_only_posting": False})
    assert say(world, p.ravi, program["id"], "see you all").status_code == 201

    seen_by_patient = next(i for i in inbox(world, p.priya)["items"] if i["id"] == program["id"])["last_message"]
    seen_by_provider = next(i for i in inbox(world, p.rao)["items"] if i["id"] == program["id"])["last_message"]

    assert seen_by_patient["sender_id"] == p.ravi["id"] and "Kumar" not in (seen_by_patient["sender_name"] or "") and seen_by_patient["sender_name"]
    assert seen_by_provider["sender_name"] == "Ravi Kumar"


def test_a_system_line_has_no_sender_and_a_hidden_message_is_not_shown(world):
    p = world.people
    group = joined_group(world, kind="care", members=[p.mehta, p.priya])

    row = next(i for i in inbox(world, p.priya)["items"] if i["id"] == group["id"])
    assert row["last_message"]["sender_id"] is None and row["last_message"]["sender_name"] is None and row["last_message"]["preview"]
    assert row["unread_count"] == 0                                                                                    # system lines are not unread messages

    kept = say(world, p.mehta, group["id"], "keep this").json()
    secret = say(world, p.mehta, group["id"], "take this back").json()
    world.as_user(p.mehta).post(f"{API}/{group['id']}/messages/{secret['id']}/retract")

    row = next(i for i in inbox(world, p.priya)["items"] if i["id"] == group["id"])
    assert row["last_message_preview"] == "keep this" and row["last_message"]["preview"] == "keep this" and "take this back" not in str(row)
    assert row["unread_count"] == 1 and kept["id"]


def test_a_member_does_not_see_what_was_said_before_they_joined(world):
    p = world.people
    group = make_group(world, p.rao, "care", invitees=[p.mehta])
    accept(world, p.mehta, group["id"])
    assert say(world, p.rao, group["id"], "before priya").status_code == 201
    world.as_user(p.rao).post(f"{API}/{group['id']}/members", json={"invitees": [{"user_id": p.priya["id"]}]})
    accept(world, p.priya, group["id"])

    row = next(i for i in inbox(world, p.priya)["items"] if i["id"] == group["id"])

    assert "before priya" not in str(row) and row["unread_count"] == 0


def test_only_accepted_groups_appear(world):
    p = world.people
    invited = make_group(world, p.rao, "care", invitees=[p.priya], name="Not yet")
    left = joined_group(world, kind="care", members=[p.priya], name="Gone")
    world.as_user(p.priya).post(f"{API}/{left['id']}/leave")

    assert inbox(world, p.priya)["items"] == []
    assert [g["conversation_id"] for g in world.as_user(p.priya).get(f"{API}/invitations").json()["items"]] == [invited["id"]]
    accept(world, p.priya, invited["id"])
    assert [i["id"] for i in inbox(world, p.priya)["items"]] == [invited["id"]]


def test_the_list_is_newest_activity_first_and_empty_chats_go_last(world):
    p = world.people
    old_chat = direct_chat(world, p.priya, p.rao, "old news")
    group = joined_group(world, kind="care", members=[p.mehta, p.priya])
    say(world, p.mehta, group["id"], "latest")
    quiet = direct_chat(world, p.priya, p.nair)
    now = datetime.utcnow()
    stamp(world, old_chat["id"], now - timedelta(days=2))
    stamp(world, group["id"], now - timedelta(hours=1))
    quiet_row = world.db.get(Conversation, UUID(quiet["id"]))
    quiet_row.last_message_at, quiet_row.last_message_preview = None, None
    world.db.commit()

    items = inbox(world, p.priya)["items"]

    assert [i["id"] for i in items] == [group["id"], old_chat["id"], quiet["id"]]
    assert items[2]["last_message_at"] is None and items[2]["last_message"] is None


def test_providers_get_the_same_list_and_filters_still_work(world):
    p = world.people
    chat = direct_chat(world, p.priya, p.rao, "hi")
    group = joined_group(world, kind="care", members=[p.mehta, p.priya])

    assert {i["id"] for i in inbox(world, p.rao)["items"]} == {chat["id"], group["id"]}
    world.as_user(p.priya).patch(f"{INBOX}{chat['id']}/archive", json={"archived": True})
    assert [i["id"] for i in inbox(world, p.priya)["items"]] == [group["id"]]
    archived = inbox(world, p.priya, status="archived")["items"]
    assert [i["id"] for i in archived] == [chat["id"]] and archived[0]["is_archived"] is True


def test_closed_and_posting_limits_show_in_can_post(world):
    p = world.people
    group = joined_group(world, kind="program", members=[p.priya])

    assert next(i for i in inbox(world, p.priya)["items"] if i["id"] == group["id"])["can_post"] is False              # providers only by default
    world.as_user(p.rao).post(f"{API}/{group['id']}/close", json={"reason": "ended"})
    row = next(i for i in inbox(world, p.rao)["items"] if i["id"] == group["id"])
    assert row["can_post"] is False and row["status"] == "closed"


def test_search_finds_a_group_by_name_but_never_by_a_retracted_message(world):
    p = world.people
    group = joined_group(world, kind="care", members=[p.mehta, p.priya], name="Knee rehab team")
    chat = direct_chat(world, p.priya, p.rao, "about the invoice")
    secret = say(world, p.mehta, group["id"], "zebra-secret").json()
    world.as_user(p.mehta).post(f"{API}/{group['id']}/messages/{secret['id']}/retract")
    stored = world.db.get(Conversation, UUID(group["id"]))
    stored.last_message_preview = "zebra-secret"                                   # the stored copy is what a naive search would match
    world.db.commit()

    assert [i["id"] for i in inbox(world, p.priya, search="knee")["items"]] == [group["id"]]
    assert [i["id"] for i in inbox(world, p.priya, search="invoice")["items"]] == [chat["id"]]
    assert inbox(world, p.priya, search="zebra")["items"] == []
    found = world.as_user(p.priya).get(f"{INBOX}search", params={"search": "knee"})
    assert found.status_code in (200, 404, 422)


def test_the_message_apis_stay_separate(world):
    p = world.people
    group = joined_group(world, kind="care", members=[p.priya])

    assert world.as_user(p.priya).get(f"{INBOX}{group['id']}/messages").status_code == 409                 # a group's messages are only at /groups
    assert world.as_user(p.priya).get(f"{API}/{group['id']}/messages").status_code == 200
