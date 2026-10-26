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
    import redis.asyncio as aioredis

    delay = 1
    while True:
        client = aioredis.from_url(_redis_url())
        try:
            pubsub = client.pubsub()
            await pubsub.subscribe(CHANNEL)
            delay = 1
            async for message in pubsub.listen():
                if message.get("type") == "message":
                    await apply_message(message["data"])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("group room listener lost Redis; retrying in %ss", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)
        finally:
            try:
                await client.aclose()
            except Exception:
                pass


def start_listener() -> None:
    """Start once per process, when Redis is configured. Must be called from the running server loop."""
    if _redis_url():
        asyncio.get_running_loop().create_task(listen())
