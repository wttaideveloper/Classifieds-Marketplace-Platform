"""Putting a person's connected devices into (or out of) a group's Socket.IO room from another process.

Socket.IO rooms live in the memory of the process that holds the connection. The Redis manager carries `emit` between processes but not
`enter_room` / `leave_room`, so when the REST API runs apart from the Socket.IO server, an accepted invitation or a removal made over
REST cannot reach the sockets directly. The REST side publishes a small message on a Redis channel instead; every process listens, and
the one holding that person's sockets moves them. Without SOCKETIO_REDIS_URL everything runs in one process and the move is made directly.
"""
import asyncio
import json
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

CHANNEL = "classifieds:group-room-sync"
_publisher = None
_listener_task = None
_listener_pubsub = None


def _redis_url() -> str:
    return settings.SOCKETIO_REDIS_URL.strip()


async def move_local(user_id: str, conversation_id: str, join: bool) -> None:
    """Move this process's sockets of `user_id`."""
    from app.realtime.rooms import conversation_room, user_room
    from app.realtime.server import sio

    for sid, _eio_sid in list(sio.manager.get_participants("/", user_room(user_id))):
        if join:
            await sio.enter_room(sid, conversation_room(conversation_id))
        else:
            await sio.leave_room(sid, conversation_room(conversation_id))


async def move(user_id: str, conversation_id: str, join: bool) -> None:
    """Called by the code that changed the membership."""
    if not _redis_url():
        await move_local(user_id, conversation_id, join)
        return
    global _publisher
    import redis.asyncio as aioredis

    if _publisher is None:
        _publisher = aioredis.from_url(_redis_url())
    await _publisher.publish(CHANNEL, json.dumps({"user_id": user_id, "conversation_id": conversation_id, "join": bool(join)}))


async def apply_message(raw) -> None:
    try:
        data = json.loads(raw)
        await move_local(str(data["user_id"]), str(data["conversation_id"]), bool(data["join"]))
    except Exception:
        logger.exception("could not apply a group room change")


async def listen() -> None:
    """Runs for the life of the process. Reconnects after a Redis failure."""
    global _listener_pubsub
    import redis.asyncio as aioredis

    delay = 1
    while True:
        client = None
        pubsub = None
        retry_delay = None
        try:
            client = aioredis.from_url(_redis_url())
            pubsub = client.pubsub()
            _listener_pubsub = pubsub
            await pubsub.subscribe(CHANNEL)
            delay = 1
            async for message in pubsub.listen():
                if message.get("type") == "message":
                    await apply_message(message["data"])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("group room listener lost Redis; retrying in %ss", delay)
            retry_delay = delay
            delay = min(delay * 2, 30)
        finally:
            if pubsub is not None:
                try:
                    await pubsub.aclose()
                except Exception:
                    logger.exception("could not close group room listener PubSub")
                finally:
                    if _listener_pubsub is pubsub:
                        _listener_pubsub = None
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    logger.exception("could not close group room listener Redis client")
        if retry_delay is not None:
            await asyncio.sleep(retry_delay)


def start_listener() -> None:
    """Start once per process, when Redis is configured. Must be called from the running server loop."""
    global _listener_task
    if not _redis_url():
        return
    if _listener_task is not None and not _listener_task.done():
        return
    _listener_task = asyncio.get_running_loop().create_task(listen())


async def stop_listener() -> None:
    """Stop the listener and close Redis resources owned by this process."""
    global _listener_task, _publisher

    task = _listener_task
    if task is not None:
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("group room listener stopped unexpectedly")
        finally:
            if _listener_task is task:
                _listener_task = None

    publisher = _publisher
    if publisher is not None:
        _publisher = None
        try:
            await publisher.aclose()
        except Exception:
            logger.exception("could not close group room publisher Redis client")
