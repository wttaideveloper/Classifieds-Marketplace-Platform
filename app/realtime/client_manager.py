import logging

import redis
import socketio

from app.core.config import settings

logger = logging.getLogger(__name__)


def ping_redis(redis_url: str) -> bool:
    try:
        client = redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
        client.ping()
        return True
    except Exception:
        logger.exception("Redis ping failed for %s", redis_url)
        return False


def build_client_manager() -> socketio.AsyncRedisManager | None:
    redis_url = settings.SOCKETIO_REDIS_URL.strip()
    if not redis_url:
        if settings.WEB_CONCURRENCY > 1:
            raise RuntimeError(
                "WEB_CONCURRENCY > 1 requires SOCKETIO_REDIS_URL for shared Socket.IO sessions"
            )
        logger.info("Socket.IO using in-memory sessions (WEB_CONCURRENCY=1)")
        return None

    if not ping_redis(redis_url):
        raise RuntimeError(
            f"Cannot connect to Redis at {redis_url}. "
            "Start Redis before the API when using multiple workers."
        )

    manager = socketio.AsyncRedisManager(redis_url)
    logger.info(
        "Socket.IO Redis manager ready at %s (WEB_CONCURRENCY=%s)",
        redis_url,
        settings.WEB_CONCURRENCY,
    )
    return manager
