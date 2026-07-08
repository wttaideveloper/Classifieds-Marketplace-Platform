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
        logger.info(
            "Socket.IO in-process mode (SOCKET_WORKERS=1). "
            "Set SOCKETIO_REDIS_URL only for split/multi-instance event broadcast."
        )
        return None

    if not ping_redis(redis_url):
        raise RuntimeError(
            f"Cannot connect to Redis at {redis_url}. "
            "Start Redis before enabling SOCKETIO_REDIS_URL."
        )

    manager = socketio.AsyncRedisManager(redis_url)
    logger.info(
        "Socket.IO Redis pub/sub enabled at %s (cross-process event broadcast)",
        redis_url,
    )
    return manager
