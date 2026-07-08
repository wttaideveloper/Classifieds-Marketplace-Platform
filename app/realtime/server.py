import socketio

from app.core.config import settings
from app.realtime.client_manager import build_client_manager

_client_manager = build_client_manager()

_cors_origins = settings.cors_origins_list
_cors_allowed = "*" if _cors_origins == ["*"] else _cors_origins

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=_cors_allowed,
    client_manager=_client_manager,
    logger=False,
    engineio_logger=False,
)

SOCKETIO_PATH = settings.normalize_socketio_path(settings.SOCKETIO_PATH)
