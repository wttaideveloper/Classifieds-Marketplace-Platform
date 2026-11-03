import asyncio
import json
import runpy
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import app.main as main
from app.realtime import room_sync


@pytest.fixture(autouse=True)
def reset_room_sync_state():
    room_sync._listener_task = None
    room_sync._listener_pubsub = None
    room_sync._publisher = None
    yield
    room_sync._listener_task = None
    room_sync._listener_pubsub = None
    room_sync._publisher = None


def test_listener_starts_once_and_retains_task(monkeypatch):
    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def fake_listen():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()

        monkeypatch.setattr(room_sync.settings, "SOCKETIO_REDIS_URL", "redis://example.invalid/0")
        monkeypatch.setattr(room_sync, "listen", fake_listen)

        room_sync.start_listener()
        task = room_sync._listener_task
        room_sync.start_listener()

        assert task is not None
        assert room_sync._listener_task is task
        await started.wait()
        assert calls == 1

        await room_sync.stop_listener()
        assert room_sync._listener_task is None

    asyncio.run(scenario())


def test_listener_cancellation_closes_pubsub_client_and_publisher_in_order(monkeypatch):
    async def scenario():
        started = asyncio.Event()
        closed = []

        class FakePubSub:
            async def subscribe(self, channel):
                assert channel == room_sync.CHANNEL

            async def listen(self):
                started.set()
                await asyncio.Event().wait()
                yield  # pragma: no cover

            async def aclose(self):
                closed.append("pubsub")

        pubsub = FakePubSub()

        class FakeClient:
            def pubsub(self):
                return pubsub

            async def aclose(self):
                closed.append("listener-client")

        class FakePublisher:
            async def aclose(self):
                closed.append("publisher")

        monkeypatch.setattr(room_sync.settings, "SOCKETIO_REDIS_URL", "redis://example.invalid/0")
        monkeypatch.setattr("redis.asyncio.from_url", lambda url: FakeClient())
        room_sync._publisher = FakePublisher()

        room_sync.start_listener()
        await started.wait()
        assert room_sync._listener_pubsub is pubsub

        await room_sync.stop_listener()

        assert closed == ["pubsub", "listener-client", "publisher"]
        assert room_sync._listener_task is None
        assert room_sync._listener_pubsub is None
        assert room_sync._publisher is None

    asyncio.run(scenario())


def test_listener_reconnects_with_exponential_backoff(monkeypatch):
    async def scenario():
        real_sleep = asyncio.sleep
        sleeps = []
        clients = []
        applied = asyncio.Event()

        class FakePubSub:
            def __init__(self, attempt):
                self.attempt = attempt
                self.closed = False

            async def subscribe(self, channel):
                if self.attempt < 3:
                    raise ConnectionError("Redis unavailable")

            async def listen(self):
                yield {
                    "type": "message",
                    "data": json.dumps({"user_id": "u1", "conversation_id": "c1", "join": True}),
                }
                await asyncio.Event().wait()

            async def aclose(self):
                self.closed = True

        class FakeClient:
            def __init__(self, attempt):
                self.pubsub_instance = FakePubSub(attempt)
                self.closed = False

            def pubsub(self):
                return self.pubsub_instance

            async def aclose(self):
                self.closed = True

        def from_url(url):
            client = FakeClient(len(clients) + 1)
            clients.append(client)
            return client

        async def no_delay(delay):
            sleeps.append(delay)
            await real_sleep(0)

        async def apply(raw):
            applied.set()

        monkeypatch.setattr(room_sync.settings, "SOCKETIO_REDIS_URL", "redis://example.invalid/0")
        monkeypatch.setattr("redis.asyncio.from_url", from_url)
        monkeypatch.setattr(room_sync.asyncio, "sleep", no_delay)
        monkeypatch.setattr(room_sync, "apply_message", apply)

        room_sync.start_listener()
        await asyncio.wait_for(applied.wait(), timeout=1)

        assert len(clients) == 3
        assert sleeps == [1, 2]
        assert all(client.closed for client in clients[:2])
        assert all(client.pubsub_instance.closed for client in clients[:2])

        await room_sync.stop_listener()
        assert clients[2].closed is True
        assert clients[2].pubsub_instance.closed is True

    asyncio.run(scenario())


def test_publisher_cleanup_is_idempotent():
    async def scenario():
        class FakePublisher:
            close_calls = 0

            async def aclose(self):
                self.close_calls += 1

        publisher = FakePublisher()
        room_sync._publisher = publisher

        await room_sync.stop_listener()
        await room_sync.stop_listener()

        assert publisher.close_calls == 1
        assert room_sync._publisher is None

    asyncio.run(scenario())


@pytest.mark.parametrize("application", [main.app, main.socket_app], ids=["rest-app", "socket-app"])
def test_application_shutdown_stops_listener(monkeypatch, application):
    stopped = []

    async def stop_listener():
        stopped.append(True)

    monkeypatch.setattr(room_sync, "start_listener", lambda: None)
    monkeypatch.setattr(room_sync, "stop_listener", stop_listener)
    monkeypatch.setattr(main, "ensure_upload_directory", lambda: Path("uploads"))
    monkeypatch.setattr(main.Base.metadata, "create_all", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.training_notifications.start_reminder_scheduler", lambda: None)
    monkeypatch.setattr("app.services.meeting_service.start_scheduler", lambda: None)
    monkeypatch.setattr("app.services.payment_jobs.start_scheduler", lambda: None)
    monkeypatch.setattr("app.services.group_data_export.start_scheduler", lambda: None)

    with TestClient(application):
        pass

    assert stopped == [True]


def test_gunicorn_control_socket_is_disabled():
    config = runpy.run_path(str(Path(__file__).parents[1] / "gunicorn.conf.py"))

    assert config["control_socket_disable"] is True
