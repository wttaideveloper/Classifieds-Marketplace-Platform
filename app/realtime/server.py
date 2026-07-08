import socketio

from app.core.config import settings

_cors_origins = settings.cors_origins_list
if _cors_origins == ["*"]:
    _cors_allowed = "*"
else:
    _cors_allowed = _cors_origins

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=_cors_allowed,
    logger=False,
    engineio_logger=False,
)

SOCKETIO_PATH = settings.SOCKETIO_PATH.rstrip("/") or "/socket.io"
