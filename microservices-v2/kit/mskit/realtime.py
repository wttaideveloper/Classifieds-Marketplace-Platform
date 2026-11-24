"""Emit Socket.IO events from any service (messaging, not HTTP).

The Socket.IO server runs in the communication service. Other services publish through Redis with a
write-only python-socketio manager - the same pub/sub channel and message format the monolith used
between its REST and socket processes (``SOCKETIO_REDIS_URL``) - so clients receive identical events.

Room names and payload serialisation are copied from ``app/realtime/rooms.py``.
Failure handling: emitting is fire-and-forget (as in the monolith); a Redis error is logged, never raised.
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime
from uuid import UUID

logger = logging.getLogger(__name__)
_manager = None
_lock = threading.Lock()


def user_room(user_id) -> str:
    return f"user:{user_id}"


def conversation_room(conversation_id) -> str:
    return f"conversation:{conversation_id}"


def serialize(value):
    if isinstance(value, dict):
        return {key: serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serialize(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return serialize(value.model_dump())
    return value


def _get_manager():
    global _manager
    from mskit.settings import settings

    url = settings.SOCKETIO_REDIS_URL.strip()
    if not url:
        return None
    with _lock:
        if _manager is None:
            import socketio

            _manager = socketio.RedisManager(url, write_only=True)
    return _manager


def emit(event: str, data, *, room: str | None = None) -> bool:
    manager = _get_manager()
    if manager is None:
        logger.warning("SOCKETIO_REDIS_URL not set: real-time event %r not delivered", event)
        return False
    try:
        manager.emit(event, serialize(data), room=room, namespace="/")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("real-time emit %r failed: %s", event, type(exc).__name__)
        return False


def emit_notification(user_id, notification: dict) -> bool:
    """Same event as app.realtime.emitters.emit_notification."""
    return emit("notification", notification, room=user_room(user_id))
