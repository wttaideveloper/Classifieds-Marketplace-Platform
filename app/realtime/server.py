import socketio

from app.core.config import settings
from app.realtime.client_manager import build_client_manager

_client_manager = build_client_manager()

_cors_origins = settings.cors_origins_list
# Engine.IO interprets [] as disabling Origin checks. With session cookies,
# an empty configuration must instead retain its same-origin default (None).
_cors_allowed = _cors_origins or None

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=_cors_allowed,
    cors_credentials=True,
    client_manager=_client_manager,
    logger=False,
    engineio_logger=False,
)

SOCKETIO_PATH = settings.normalize_socketio_path(settings.SOCKETIO_PATH)
