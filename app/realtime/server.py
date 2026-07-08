import logging

import socketio

from app.core.config import settings

logger = logging.getLogger(__name__)

_cors_origins = settings.cors_origins_list
if _cors_origins == ["*"]:
    _cors_allowed = "*"
else:
    _cors_allowed = _cors_origins

_client_manager = None
_redis_url = settings.SOCKETIO_REDIS_URL.strip()
if _redis_url:
    try:
        _client_manager = socketio.AsyncRedisManager(_redis_url)
        logger.info("Socket.IO using Redis client manager at %s", _redis_url)
    except Exception:
        logger.exception("Failed to initialize Socket.IO Redis manager")
        raise

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=_cors_allowed,
    client_manager=_client_manager,
    logger=False,
    engineio_logger=False,
)

SOCKETIO_PATH = settings.normalize_socketio_path(settings.SOCKETIO_PATH)
