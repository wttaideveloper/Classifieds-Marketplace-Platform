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
