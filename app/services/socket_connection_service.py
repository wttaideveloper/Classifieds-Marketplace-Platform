import os

import redis

from app.core.config import settings
from app.realtime.client_manager import ping_redis
from app.realtime.server import SOCKETIO_PATH


def build_socket_connection_info() -> dict:
    base_url = settings.PUBLIC_API_BASE_URL.strip().rstrip("/")
    if not base_url:
        base_url = settings.FRONTEND_URL.strip().rstrip("/")

    path = SOCKETIO_PATH if SOCKETIO_PATH.startswith("/") else f"/{SOCKETIO_PATH}"
    polling_test_url = f"{base_url}{path}?EIO=4&transport=polling"
    redis_url = settings.SOCKETIO_REDIS_URL.strip()

    notes = [
        "Socket.IO is mounted on the same Gunicorn app as FastAPI (`app.main:socket_app`).",
        f"Configured SOCKETIO_PATH={path}",
        (
            "Engine.IO polling sessions are in-memory per worker. "
            "Combined deploy requires SOCKET_WORKERS=1 (WEB_CONCURRENCY=1)."
        ),
    ]

    if redis_url:
        notes.append(
            f"Redis ({redis_url}) is used for Socket.IO event pub/sub across processes — "
            "NOT for Engine.IO sid stickiness on the same Gunicorn port."
        )
    else:
        notes.append("Redis not configured (fine for single-worker combined deploy).")

    if settings.WEB_CONCURRENCY > 1 or settings.SOCKET_WORKERS > 1:
        notes.append(
            "ERROR: Multiple workers detected — polling will return 400 Invalid session. "
            "Set SOCKET_WORKERS=1 and restart."
        )

    if path == "/api/socket.io":
        notes.append(
            "Frontend: io(baseUrl, { path: '/api/socket.io', auth: { token } })"
        )

    return {
        "connection_url": base_url,
        "connection_path": path,
        "polling_test_url": polling_test_url,
        "web_concurrency": settings.WEB_CONCURRENCY,
        "socket_workers": settings.SOCKET_WORKERS,
        "redis_enabled": bool(redis_url),
        "redis_reachable": ping_redis(redis_url) if redis_url else None,
        "process_pid": os.getpid(),
        "auth": {
            "type": "HttpOnly web-session cookie or JWT Bearer",
            "connect": f"io('{base_url}', {{ path: '{path}', withCredentials: true }})",
            "bearer_fallback": f"io('{base_url}', {{ path: '{path}', auth: {{ token: '<JWT>' }} }})",
        },
        "deployment_notes": notes,
    }
