"""Centralized user-level presence tracking.

Presence must reflect the application USER's state, not any single
Socket.IO connection. A user can hold several concurrent sockets (a web
tab, the mobile app, multiple browser tabs/devices); presence should only
flip to offline once every socket has gone away and stayed away for a
short grace period. Without this, a single tab refresh, mobile
foreground/background cycle, or transport upgrade reconnect on ANY one
socket produces a false `user_offline` for the whole user while another
socket (or a fast reconnect of the same one) is still around.

Both Socket.IO (`app.realtime.events`) and the REST presence endpoint
(`PUT /presence/status`) funnel through `transition_online`/
`transition_offline` here, so there is exactly one place that decides
whether presence actually changed and whether an event should be emitted.

Active sockets are tracked per user in Redis (`SOCKETIO_REDIS_URL`) when
configured — the same Redis already used for Socket.IO's cross-instance
pub/sub in `app.realtime.client_manager` — so multiple socket-server
instances/workers share one accurate active-socket count instead of each
seeing only its own sockets. With no Redis configured, the deployment is a
single combined-mode process (`SOCKET_WORKERS` is pinned to 1), so an
in-process registry is safe and avoids adding an infrastructure dependency
that isn't needed yet.
"""

import asyncio
import logging

from app.core.config import settings
from app.db.database import SessionLocal
from app.repository import chat_repo

logger = logging.getLogger(__name__)

_REGISTRY_KEY_PREFIX = "presence:sockets:"


class _InMemorySocketRegistry:
    """Single-process active-socket registry (no Redis configured).

    Safe without locking: every method runs to completion with no `await`
    inside it, so asyncio never interleaves another coroutine mid-mutation.
    """

    def __init__(self):
        self._sockets: dict[str, set[str]] = {}

    async def add(self, user_id: str, sid: str) -> int:
        sids = self._sockets.setdefault(user_id, set())
        sids.add(sid)
        return len(sids)

    async def remove(self, user_id: str, sid: str) -> int:
        sids = self._sockets.get(user_id)
        if not sids:
            return 0
        sids.discard(sid)
        if not sids:
            self._sockets.pop(user_id, None)
            return 0
        return len(sids)

    async def count(self, user_id: str) -> int:
        return len(self._sockets.get(user_id, ()))


class _RedisSocketRegistry:
    """Active-socket registry shared across socket-server instances/workers via Redis."""

    def __init__(self, redis_url: str):
        import redis.asyncio as redis_asyncio

        self._client = redis_asyncio.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )

    @staticmethod
    def _key(user_id: str) -> str:
        return f"{_REGISTRY_KEY_PREFIX}{user_id}"

    async def add(self, user_id: str, sid: str) -> int:
        key = self._key(user_id)
        await self._client.sadd(key, sid)
        return await self._client.scard(key)

    async def remove(self, user_id: str, sid: str) -> int:
        key = self._key(user_id)
        await self._client.srem(key, sid)
        # Redis drops a set automatically once its last member is removed —
        # no explicit DEL, which would race a concurrent SADD from another
        # instance reconnecting the same user in between.
        return await self._client.scard(key)

    async def count(self, user_id: str) -> int:
        return await self._client.scard(self._key(user_id))


def _build_registry():
    redis_url = settings.SOCKETIO_REDIS_URL.strip()
    if not redis_url:
        logger.info("Presence socket registry: in-process (no SOCKETIO_REDIS_URL configured).")
        return _InMemorySocketRegistry()
    logger.info("Presence socket registry: Redis-backed at %s (shared across instances).", redis_url)
    return _RedisSocketRegistry(redis_url)


_registry = _build_registry()

# Per-process map of user_id -> pending "transition to offline" task, so a
# reconnect on THIS instance can cancel its own grace timer outright. A
# reconnect on a DIFFERENT instance can't reach into this dict, so the timer
# itself always re-checks the shared registry before acting (see
# `_schedule_offline_grace`) — that recheck is what actually guarantees
# correctness; this dict is just a same-instance optimization.
_pending_offline_tasks: dict[str, asyncio.Task] = {}


async def _emit_online(user_id: str, *, skip_sid: str | None = None) -> None:
    from app.realtime.emitters import emit_user_online

    await emit_user_online(user_id, skip_sid=skip_sid)


async def _emit_offline(user_id: str) -> None:
    from app.realtime.emitters import emit_user_offline

    await emit_user_offline(user_id)


def _set_status(user_id: str, status: str) -> bool:
    """Synchronous DB write shared by both online/offline transitions.

    Returns True only when the status actually changed.
    """
    db = SessionLocal()
    try:
        _presence, changed = chat_repo.update_presence(db, user_id, status)
        return changed
    finally:
        db.close()


async def transition_online(user_id: str, *, skip_sid: str | None = None) -> bool:
    """Mark the user online if they weren't already.

    Emits `user_online` only when the user's aggregate state genuinely
    changed from offline -> online — never once per socket.
    """
    changed = _set_status(user_id, "online")
    if changed:
        await _emit_online(user_id, skip_sid=skip_sid)
    return changed


async def transition_offline(user_id: str) -> bool:
    """Mark the user offline if they weren't already.

    Emits `user_offline` (and bumps last_seen_at, via chat_repo.update_presence)
    only when the user's aggregate state genuinely changed to offline.
    """
    changed = _set_status(user_id, "offline")
    if changed:
        await _emit_offline(user_id)
    return changed


async def handle_connect(user_id: str, sid: str) -> None:
    """Register a newly connected socket and bring the user online if needed."""
    pending = _pending_offline_tasks.pop(user_id, None)
    if pending and not pending.done():
        pending.cancel()

    await _registry.add(user_id, sid)
    await transition_online(user_id, skip_sid=sid)


async def handle_disconnect(user_id: str, sid: str) -> None:
    """Unregister a socket.

    Only the user's LAST active socket going away starts the offline grace
    period — while any other socket remains, the user stays online and no
    `user_offline` is emitted.
    """
    remaining = await _registry.remove(user_id, sid)
    if remaining > 0:
        return

    _schedule_offline_grace(user_id)


def _schedule_offline_grace(user_id: str) -> None:
    existing = _pending_offline_tasks.get(user_id)
    if existing and not existing.done():
        existing.cancel()

    async def _grace_then_offline():
        try:
            await asyncio.sleep(settings.PRESENCE_OFFLINE_GRACE_SECONDS)
            # Re-check the shared registry, not a snapshot taken at
            # schedule-time — a reconnect during the grace window, on THIS
            # instance or any other one behind the same Redis, aborts the
            # transition here.
            if await _registry.count(user_id) == 0:
                await transition_offline(user_id)
        except asyncio.CancelledError:
            pass
        finally:
            if _pending_offline_tasks.get(user_id) is task:
                _pending_offline_tasks.pop(user_id, None)

    task = asyncio.create_task(_grace_then_offline())
    _pending_offline_tasks[user_id] = task


def schedule_presence_emit(user_id: str, status: str) -> None:
    """Fire-and-forget socket emit for a REST-driven presence change.

    `PUT /presence/status` is a sync FastAPI route (runs on the sync
    threadpool) so it can't `await`; this schedules the emit onto the
    running loop the same way notification_delivery_service's
    `_emit_realtime_notification` does for REST-triggered notifications.
    """
    if status not in ("online", "offline"):
        return

    coro = _emit_online(user_id) if status == "online" else _emit_offline(user_id)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(coro)
        else:
            loop.run_until_complete(coro)
    except Exception:
        logger.exception(
            "Failed to emit presence transition for user_id=%s status=%s", user_id, status
        )
