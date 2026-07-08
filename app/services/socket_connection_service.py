from app.core.config import settings
from app.realtime.server import SOCKETIO_PATH


def build_socket_connection_info() -> dict:
    base_url = settings.PUBLIC_API_BASE_URL.strip().rstrip("/")
    if not base_url:
        base_url = settings.FRONTEND_URL.strip().rstrip("/")

    path = SOCKETIO_PATH if SOCKETIO_PATH.startswith("/") else f"/{SOCKETIO_PATH}"
    polling_test_url = f"{base_url}{path}?EIO=4&transport=polling"

    notes = [
        "Socket.IO is mounted on the same Gunicorn app as FastAPI (`app.main:socket_app`).",
        f"Configured SOCKETIO_PATH={path}",
    ]

    if path == "/api/socket.io":
        notes.append(
            "Production uses /api/socket.io because only /api/* is proxied to the backend."
        )
        notes.append(
            "Frontend: io(baseUrl, { path: '/api/socket.io', auth: { token } })"
        )
    else:
        notes.append(
            "If /socket.io returns a frontend 404, set SOCKETIO_PATH=/api/socket.io "
            "or add an nginx location for /socket.io with WebSocket upgrade headers."
        )

    return {
        "connection_url": base_url,
        "connection_path": path,
        "polling_test_url": polling_test_url,
        "auth": {
            "type": "JWT Bearer",
            "connect": f"io('{base_url}', {{ path: '{path}', auth: {{ token: '<JWT>' }} }})",
        },
        "deployment_notes": notes,
    }
