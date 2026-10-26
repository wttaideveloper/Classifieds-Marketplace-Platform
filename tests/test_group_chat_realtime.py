"""Group chat realtime: `conversation_type` on every group event, the `group_read` event and who gets it, and moving sockets
between rooms when the REST API and the Socket.IO server are separate processes."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.realtime import emitters, room_sync
from app.services import group_chat_notify
from tests.group_chat_support import joined_group, say, uid, world  # noqa: F401

API = "/api/v1/groups"
REAL_EMIT_GROUP, REAL_EMIT_USER, REAL_SYNC_ROOMS = group_chat_notify.emit_group, group_chat_notify.emit_user, group_chat_notify.sync_rooms


@pytest.fixture
def program(world):
    p = world.people
    return joined_group(world, kind="program", members=[p.mehta, p.priya, p.ravi])


@pytest.fixture
def care(world):
    p = world.people
    return joined_group(world, kind="care", members=[p.mehta, p.priya])


class FakeSio:
    def __init__(self):
        self.sent = []

    async def emit(self, event, payload, room=None, skip_sid=None, to=None):
        self.sent.append((event, payload, room))


@pytest.fixture
def fake_sio(monkeypatch):
    sio = FakeSio()
    monkeypatch.setattr("app.realtime.server.sio", sio)
    monkeypatch.setattr(emitters, "sio", sio)
    monkeypatch.setattr("app.realtime.loop_bridge.run_coroutine", lambda coro: asyncio.run(coro))
    return sio


# --- conversation_type ---------------------------------------------------------------------------------------------------------------------

def test_every_group_event_to_the_room_says_it_is_a_group(fake_sio):
    REAL_EMIT_GROUP("11111111-1111-1111-1111-111111111111", "message_pinned", {"message_id": "m1", "pinned": True})

    event, payload, room = fake_sio.sent[0]
    assert event == "message_pinned" and room == "conversation:11111111-1111-1111-1111-111111111111"
    assert payload == {"conversation_id": "11111111-1111-1111-1111-111111111111", "conversation_type": "group", "message_id": "m1", "pinned": True}


def test_every_group_event_to_one_person_says_it_is_a_group(fake_sio):
    REAL_EMIT_USER("22222222-2222-2222-2222-222222222222", "group_invitation", {"conversation_id": "c1"})

    event, payload, room = fake_sio.sent[0]
    assert room == "user:22222222-2222-2222-2222-222222222222" and payload == {"conversation_id": "c1", "conversation_type": "group"}


def test_the_one_to_one_message_event_is_unchanged_and_a_group_one_is_marked(fake_sio):
    asyncio.run(emitters.emit_new_message("c1", {"id": "m1"}))
    asyncio.run(emitters.emit_new_message("c2", {"id": "m2"}, extra={"conversation_type": "group"}))

    assert fake_sio.sent[0][1] == {"conversation_id": "c1", "message": {"id": "m1"}}
    assert fake_sio.sent[1][1] == {"conversation_id": "c2", "conversation_type": "group", "message": {"id": "m2"}}


# --- group_read ----------------------------------------------------------------------------------------------------------------------------

def read_events(world):
    return [(e[1], e[3]) for e in world.events if e[0] == "user" and e[2] == "group_read"]


def test_in_a_care_group_every_member_hears_that_someone_read(world, care):
    p = world.people
    assert say(world, p.mehta, care["id"], "hello").status_code == 201
    world.events.clear()

    resp = world.as_user(p.priya).post(f"{API}/{care['id']}/read")

    assert resp.status_code == 200 and set(resp.json()) == {"conversation_id", "read_at"}          # the response is unchanged
    got = read_events(world)
    assert {user for user, _ in got} == {p.rao["id"], p.mehta["id"], p.priya["id"]}
    for _, payload in got:
        assert payload == {"conversation_id": care["id"], "user_id": p.priya["id"], "read_at": resp.json()["read_at"]} or \
            (payload["conversation_id"] == care["id"] and payload["user_id"] == p.priya["id"] and payload["read_at"])


def test_in_a_program_group_patients_never_hear_who_read(world, program):
    p = world.people
    world.events.clear()

    world.as_user(p.priya).post(f"{API}/{program['id']}/read")

    assert {user for user, _ in read_events(world)} == {p.rao["id"], p.mehta["id"], p.priya["id"]}           # providers and the reader's own devices


def test_a_provider_reading_in_a_program_group_is_not_shown_to_patients(world, program):
    p = world.people
    world.events.clear()

    world.as_user(p.mehta).post(f"{API}/{program['id']}/read")

    assert p.ravi["id"] not in {user for user, _ in read_events(world)} and p.priya["id"] not in {user for user, _ in read_events(world)}


def test_nobody_outside_the_group_can_mark_it_read(world, program):
    world.events.clear()

    assert world.as_user(world.people.stranger).post(f"{API}/{program['id']}/read").status_code == 403 and not read_events(world)


def test_a_group_message_cannot_be_marked_read_one_by_one_through_the_generic_endpoint(world, program):
    p = world.people
    sent = say(world, p.rao, program["id"], "hello").json()
    world.events.clear()

    resp = world.as_user(p.ravi).patch(f"/api/v1/messages/{sent['id']}/read")

    assert resp.status_code == 409 and not read_events(world)


# --- moving sockets between rooms ----------------------------------------------------------------------------------------------------------

def test_without_redis_the_sockets_are_moved_directly(monkeypatch):
    moved = []

    async def local(user, conversation, join):
        moved.append((user, conversation, join))

    monkeypatch.setattr(room_sync.settings, "SOCKETIO_REDIS_URL", "")
    monkeypatch.setattr(room_sync, "move_local", local)
    monkeypatch.setattr("app.realtime.loop_bridge.run_coroutine", lambda coro: asyncio.run(coro))

    REAL_SYNC_ROOMS("u1", "c1", join=True)

    assert moved == [("u1", "c1", True)]


def test_with_redis_the_change_is_published_for_whichever_process_holds_the_sockets(monkeypatch):
    published = []

    class FakeRedis:
        async def publish(self, channel, message):
            published.append((channel, json.loads(message)))

    monkeypatch.setattr(room_sync.settings, "SOCKETIO_REDIS_URL", "redis://example.invalid:6379/0")
    monkeypatch.setattr(room_sync, "_publisher", FakeRedis())
    monkeypatch.setattr("app.realtime.loop_bridge.run_coroutine", lambda coro: asyncio.run(coro))

    REAL_SYNC_ROOMS("u1", "c1", join=False)

    assert published == [(room_sync.CHANNEL, {"user_id": "u1", "conversation_id": "c1", "join": False})]


def test_the_listener_moves_the_sockets_it_holds_and_ignores_garbage(monkeypatch):
    moved = []

    async def local(user, conversation, join):
        moved.append((user, conversation, join))

    monkeypatch.setattr(room_sync, "move_local", local)

    asyncio.run(room_sync.apply_message(json.dumps({"user_id": "u1", "conversation_id": "c1", "join": True})))
    asyncio.run(room_sync.apply_message(b"not json"))
    asyncio.run(room_sync.apply_message(json.dumps({"user_id": "u1"})))

    assert moved == [("u1", "c1", True)]


def test_move_local_uses_the_rooms_of_the_users_connected_sockets(monkeypatch):
    entered, left = [], []

    class Manager:
        def get_participants(self, namespace, room):
            assert room == "user:u1"
            return [("sid-a", "eio-a"), ("sid-b", "eio-b")]

    class Sio:
        manager = Manager()

        async def enter_room(self, sid, room):
            entered.append((sid, room))

        async def leave_room(self, sid, room):
            left.append((sid, room))

    monkeypatch.setattr("app.realtime.server.sio", Sio())

    asyncio.run(room_sync.move_local("u1", "c1", True))
    asyncio.run(room_sync.move_local("u1", "c1", False))

    assert entered == [("sid-a", "conversation:c1"), ("sid-b", "conversation:c1")] and left == [("sid-a", "conversation:c1"), ("sid-b", "conversation:c1")]


# --- read_summary (the viewer's own messages only) and read_through_message_id --------------------------------------------------------------

def listed(world, user, group_id):
    resp = world.as_user(user).get(f"{API}/{group_id}/messages")
    assert resp.status_code == 200, resp.text
    return {m["id"]: m for m in resp.json()["items"]}


def test_a_sender_sees_how_many_have_read_their_message_in_a_care_group(world, care):
    p = world.people
    sent = say(world, p.rao, care["id"], "hello team")
    assert sent.status_code == 201
    mid = sent.json()["id"]
    assert sent.json()["read_summary"] == {"available": True, "read_count": 0, "recipient_count": 2, "all_read": False, "reason": None}

    world.as_user(p.priya).post(f"{API}/{care['id']}/read")
    assert listed(world, p.rao, care["id"])[mid]["read_summary"] == {"available": True, "read_count": 1, "recipient_count": 2, "all_read": False, "reason": None}

    world.as_user(p.mehta).post(f"{API}/{care['id']}/read")
    assert listed(world, p.rao, care["id"])[mid]["read_summary"]["all_read"] is True


def test_read_counts_are_for_your_own_messages_only(world, care):
    p = world.people
    mid = say(world, p.rao, care["id"], "from the owner").json()["id"]

    assert listed(world, p.priya, care["id"])[mid]["read_summary"] == {
        "available": False, "read_count": None, "recipient_count": None, "all_read": None, "reason": "not_your_message"}


def test_system_lines_and_hidden_messages_have_no_counts(world, care):
    p = world.people
    mid = say(world, p.rao, care["id"], "oops").json()["id"]
    assert world.as_user(p.rao).post(f"{API}/{care['id']}/messages/{mid}/retract").status_code == 200

    items = listed(world, p.rao, care["id"])
    assert items[mid]["read_summary"]["reason"] == "hidden" and items[mid]["read_summary"]["available"] is False
    system = [m for m in items.values() if m["message_type"] == "system"]
    assert system and all(m["read_summary"]["reason"] == "system_message" for m in system)


def test_in_a_program_group_providers_get_counts_and_patients_do_not(world, program):
    p = world.people
    provider_msg = say(world, p.rao, program["id"], "welcome").json()["id"]

    assert listed(world, p.rao, program["id"])[provider_msg]["read_summary"] == {
        "available": True, "read_count": 0, "recipient_count": 3, "all_read": False, "reason": None}
    world.as_user(p.ravi).post(f"{API}/{program['id']}/read")
    assert listed(world, p.rao, program["id"])[provider_msg]["read_summary"]["read_count"] == 1
    mine = listed(world, p.priya, program["id"])
    assert mine[provider_msg]["read_summary"] == {"available": False, "read_count": None, "recipient_count": None, "all_read": None, "reason": "not_shown_to_patients"}
    assert mine[provider_msg]["read_by"] is None                                                         # unchanged


def test_someone_who_leaves_drops_out_of_the_counts(world, program):
    p = world.people
    mid = say(world, p.rao, program["id"], "hello").json()["id"]
    world.as_user(p.rao).delete(f"{API}/{program['id']}/members/{p.ravi['id']}")

    assert listed(world, p.rao, program["id"])[mid]["read_summary"]["recipient_count"] == 2


def test_group_read_names_the_newest_message_that_was_read(world, care):
    p = world.people
    first = say(world, p.rao, care["id"], "one").json()["id"]
    second = say(world, p.rao, care["id"], "two").json()["id"]
    world.events.clear()

    resp = world.as_user(p.priya).post(f"{API}/{care['id']}/read")

    got = {user: payload for user, payload in read_events(world)}
    assert got[p.rao["id"]]["read_through_message_id"] == second != first
    assert got[p.rao["id"]]["read_at"].isoformat() == resp.json()["read_at"]                                # the saved time, same in both
    assert set(got[p.rao["id"]]) == {"conversation_id", "user_id", "read_at", "read_through_message_id"}


def test_a_message_sent_after_the_read_is_not_part_of_it(world, care):
    p = world.people
    before = say(world, p.rao, care["id"], "before").json()["id"]
    world.as_user(p.priya).post(f"{API}/{care['id']}/read")
    after = say(world, p.rao, care["id"], "after").json()["id"]
    world.events.clear()
    world.as_user(p.mehta).post(f"{API}/{care['id']}/read")

    through = {user: payload for user, payload in read_events(world)}[p.rao["id"]]["read_through_message_id"]
    assert through == after and before != after
    assert listed(world, p.rao, care["id"])[after]["read_summary"]["read_count"] == 1                    # only mehta has read the later one
    assert listed(world, p.rao, care["id"])[before]["read_summary"]["read_count"] == 2


def test_group_read_has_no_message_when_nothing_but_system_lines_exist(world, care):
    p = world.people
    world.events.clear()

    world.as_user(p.priya).post(f"{API}/{care['id']}/read")

    assert {user: payload for user, payload in read_events(world)}[p.rao["id"]]["read_through_message_id"] is None
