"""Focused tests for the centralized presence transition logic.

Covers: multi-socket aggregation, the disconnect/reconnect grace period,
REST <-> Socket.IO consistency, multi-user isolation, duplicate-disconnect
safety, and the Redis-backed registry used for multi-worker/instance
deployments.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import presence_service

USER_1 = "550e8400-e29b-41d4-a716-446655440001"
USER_2 = "550e8400-e29b-41d4-a716-446655440002"


class FakePresenceStore:
    """In-memory stand-in for the user_presence table's status column,
    mirroring chat_repo.update_presence's (presence, changed) contract."""

    def __init__(self):
        self._status: dict[str, str] = {}
        self.last_seen_updates: list[str] = []

    def update_presence(self, db, user_id, status):
        user_id = str(user_id)
        changed = self._status.get(user_id, "offline") != status
        self._status[user_id] = status
        if changed:
            self.last_seen_updates.append(user_id)
        presence = MagicMock(status=status)
        return presence, changed

    def status_of(self, user_id: str) -> str:
        return self._status.get(user_id, "offline")


@pytest.fixture
def presence_env(monkeypatch):
    """Isolate presence_service's module-level state per test: a fresh
    in-memory socket registry, a fake DB-backed status store, spies on the
    two emit points, and a short grace period so grace-path tests run fast.
    """
    monkeypatch.setattr(presence_service, "_registry", presence_service._InMemorySocketRegistry())
    monkeypatch.setattr(presence_service, "_pending_offline_tasks", {})
    monkeypatch.setattr(presence_service, "SessionLocal", MagicMock())
    monkeypatch.setattr(presence_service.settings, "PRESENCE_OFFLINE_GRACE_SECONDS", 0.05)

    store = FakePresenceStore()
    monkeypatch.setattr(presence_service.chat_repo, "update_presence", store.update_presence)

    online = AsyncMock()
    offline = AsyncMock()
    monkeypatch.setattr(presence_service, "_emit_online", online)
    monkeypatch.setattr(presence_service, "_emit_offline", offline)

    return store, online, offline


async def _settle():
    """Let scheduled grace tasks run past a short grace period."""
    await asyncio.sleep(0.15)


# --- A/B: connect aggregation -------------------------------------------------

def test_first_socket_connect_brings_user_online(presence_env):
    store, online, offline = presence_env

    asyncio.run(presence_service.handle_connect(USER_1, "sidA"))

    assert store.status_of(USER_1) == "online"
    online.assert_awaited_once()
    offline.assert_not_called()


def test_second_socket_connect_does_not_duplicate_online_event(presence_env):
    store, online, offline = presence_env

    async def _run():
        await presence_service.handle_connect(USER_1, "sidA")
        await presence_service.handle_connect(USER_1, "sidB")

    asyncio.run(_run())

    online.assert_awaited_once()
    offline.assert_not_called()


# --- C/D: disconnect with another socket remaining / grace start -------------

def test_one_of_two_sockets_disconnecting_keeps_user_online(presence_env):
    store, online, offline = presence_env

    async def _run():
        await presence_service.handle_connect(USER_1, "sidA")
        await presence_service.handle_connect(USER_1, "sidB")
        await presence_service.handle_disconnect(USER_1, "sidA")
        await _settle()

    asyncio.run(_run())

    assert store.status_of(USER_1) == "online"
    offline.assert_not_called()
    assert USER_1 not in presence_service._pending_offline_tasks


def test_last_socket_disconnect_starts_grace_period_without_immediate_offline(presence_env):
    store, online, offline = presence_env

    async def _run():
        await presence_service.handle_connect(USER_1, "sidA")
        await presence_service.handle_disconnect(USER_1, "sidA")
        # Immediately after disconnect, before the grace period elapses.
        assert USER_1 in presence_service._pending_offline_tasks
        assert store.status_of(USER_1) == "online"
        offline.assert_not_called()
        presence_service._pending_offline_tasks[USER_1].cancel()
        await asyncio.sleep(0)

    asyncio.run(_run())


# --- E/F: grace period outcomes -----------------------------------------------

def test_reconnect_within_grace_period_suppresses_false_offline(presence_env):
    store, online, offline = presence_env

    async def _run():
        await presence_service.handle_connect(USER_1, "sidA")
        await presence_service.handle_disconnect(USER_1, "sidA")
        await presence_service.handle_connect(USER_1, "sidA")  # reconnect before grace elapses
        await _settle()

    asyncio.run(_run())

    assert store.status_of(USER_1) == "online"
    offline.assert_not_called()
    online.assert_awaited_once()  # only the original offline->online transition emitted


def test_no_reconnect_after_grace_transitions_offline_exactly_once(presence_env):
    store, online, offline = presence_env

    async def _run():
        await presence_service.handle_connect(USER_1, "sidA")
        await presence_service.handle_disconnect(USER_1, "sidA")
        await _settle()

    asyncio.run(_run())

    assert store.status_of(USER_1) == "offline"
    offline.assert_awaited_once()
    assert USER_1 in store.last_seen_updates  # last_seen_at only bumped on the genuine transition


# --- G: offline user reconnects ------------------------------------------------

def test_offline_user_reconnecting_goes_online_once(presence_env):
    store, online, offline = presence_env

    async def _run():
        await presence_service.handle_connect(USER_1, "sidA")
        await presence_service.handle_disconnect(USER_1, "sidA")
        await _settle()  # user genuinely goes offline
        await presence_service.handle_connect(USER_1, "sidB")

    asyncio.run(_run())

    assert store.status_of(USER_1) == "online"
    assert online.await_count == 2  # initial connect + reconnect after genuine offline
    offline.assert_awaited_once()


# --- H/I: REST <-> socket consistency ------------------------------------------

def test_rest_online_survives_temporary_socket_disconnect_reconnect(presence_env):
    store, online, offline = presence_env

    async def _run():
        await presence_service.handle_connect(USER_1, "sidA")
        changed = await presence_service.transition_online(USER_1)  # redundant REST call
        assert changed is False
        await presence_service.handle_disconnect(USER_1, "sidA")
        await presence_service.handle_connect(USER_1, "sidA")  # temporary blip, reconnects
        await _settle()

    asyncio.run(_run())

    assert store.status_of(USER_1) == "online"
    offline.assert_not_called()
    online.assert_awaited_once()


def test_rest_offline_transition_emits_once_and_is_idempotent(presence_env):
    store, online, offline = presence_env

    async def _run():
        await presence_service.handle_connect(USER_1, "sidA")
        first = await presence_service.transition_offline(USER_1)
        second = await presence_service.transition_offline(USER_1)
        return first, second

    first, second = asyncio.run(_run())

    assert first is True
    assert second is False
    offline.assert_awaited_once()


# --- J: multi-user isolation ---------------------------------------------------

def test_multiple_users_do_not_interfere(presence_env):
    store, online, offline = presence_env

    async def _run():
        await presence_service.handle_connect(USER_1, "sidA")
        await presence_service.handle_connect(USER_2, "sidB")
        await presence_service.handle_disconnect(USER_1, "sidA")
        await _settle()

    asyncio.run(_run())

    assert store.status_of(USER_1) == "offline"
    assert store.status_of(USER_2) == "online"
    offline.assert_awaited_once_with(USER_1)


# --- L: duplicate disconnect safety --------------------------------------------

def test_duplicate_disconnect_does_not_corrupt_active_socket_count(presence_env):
    store, online, offline = presence_env

    async def _run():
        await presence_service.handle_connect(USER_1, "sidA")
        await presence_service.handle_connect(USER_1, "sidB")
        await presence_service.handle_disconnect(USER_1, "sidA")
        await presence_service.handle_disconnect(USER_1, "sidA")  # duplicate callback
        return await presence_service._registry.count(USER_1)

    remaining = asyncio.run(_run())

    assert remaining == 1  # sidB still active
    offline.assert_not_called()


# --- K: Redis-backed registry (multi-worker/instance safety) ------------------

def test_build_registry_uses_redis_when_configured(monkeypatch):
    settings_mock = MagicMock()
    settings_mock.SOCKETIO_REDIS_URL = "redis://127.0.0.1:6379/0"
    monkeypatch.setattr(presence_service, "settings", settings_mock)

    with patch("redis.asyncio.from_url") as mock_from_url:
        registry = presence_service._build_registry()

    assert isinstance(registry, presence_service._RedisSocketRegistry)
    mock_from_url.assert_called_once()
    assert mock_from_url.call_args.args[0] == "redis://127.0.0.1:6379/0"


def test_build_registry_falls_back_to_in_process_without_redis_url(monkeypatch):
    settings_mock = MagicMock()
    settings_mock.SOCKETIO_REDIS_URL = ""
    monkeypatch.setattr(presence_service, "settings", settings_mock)

    registry = presence_service._build_registry()

    assert isinstance(registry, presence_service._InMemorySocketRegistry)


class _FakeAsyncRedis:
    """Minimal in-memory stand-in for redis.asyncio's set commands, so the
    Redis-backed registry's SADD/SREM/SCARD semantics are exercised without
    a live Redis server — this is what makes tracking correct when multiple
    Gunicorn workers/socket-server instances share one Redis."""

    def __init__(self):
        self._sets: dict[str, set[str]] = {}

    async def sadd(self, key, member):
        self._sets.setdefault(key, set()).add(member)

    async def srem(self, key, member):
        self._sets.get(key, set()).discard(member)

    async def scard(self, key):
        return len(self._sets.get(key, ()))


def test_redis_registry_tracks_multiple_sockets_across_calls():
    with patch("redis.asyncio.from_url", return_value=_FakeAsyncRedis()):
        registry = presence_service._RedisSocketRegistry("redis://127.0.0.1:6379/0")

    async def _run():
        await registry.add(USER_1, "sidA")
        count_after_second = await registry.add(USER_1, "sidB")
        count_after_one_removed = await registry.remove(USER_1, "sidA")
        count_after_all_removed = await registry.remove(USER_1, "sidB")
        return count_after_second, count_after_one_removed, count_after_all_removed

    second, one_removed, all_removed = asyncio.run(_run())

    assert second == 2
    assert one_removed == 1
    assert all_removed == 0


# --- REST endpoint wiring: chat_service.update_presence_service ---------------

@patch("app.services.chat_service.chat_repo")
def test_update_presence_service_emits_only_when_status_actually_changes(mock_repo):
    from app.services.chat_service import update_presence_service

    presence_row = MagicMock(user_id=USER_1, status="online", last_seen_at="2026-01-01T00:00:00")
    mock_repo.update_presence.return_value = (presence_row, True)

    with patch("app.services.presence_service.schedule_presence_emit") as mock_schedule:
        update_presence_service(MagicMock(), {"id": USER_1}, "online")
        mock_schedule.assert_called_once_with(USER_1, "online")

    mock_repo.update_presence.return_value = (presence_row, False)
    with patch("app.services.presence_service.schedule_presence_emit") as mock_schedule:
        update_presence_service(MagicMock(), {"id": USER_1}, "online")
        mock_schedule.assert_not_called()
