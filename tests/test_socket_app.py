from starlette.testclient import TestClient

from app.main import socket_app
from app.realtime.server import SOCKETIO_PATH


def test_engineio_polling_handshake():
    client = TestClient(socket_app)
    response = client.get(f"{SOCKETIO_PATH}?EIO=4&transport=polling")

    assert response.status_code == 200
    assert response.text.startswith("0")


def test_production_resolves_socketio_path_to_api_prefix():
    from app.core.config import Settings

    settings = Settings.model_validate(
        {
            "ENVIRONMENT": "production",
            "DATABASE_URL": "postgresql+psycopg2://u:p@localhost/db",
            "SECRET_KEY": "test-secret-key",
            "GOOGLE_CLIENT_ID": "id",
            "GOOGLE_CLIENT_SECRET": "secret",
            "GOOGLE_REDIRECT_URI": "https://app.example.com/callback",
            "email_user": "test@example.com",
            "email_pass": "pass",
            "FRONTEND_URL": "http://13.207.85.164",
            "CORS_ORIGINS": "http://13.207.85.164",
            "SOCKETIO_PATH": "/socket.io",
            "SOCKETIO_STANDALONE": False,
            "PUBLIC_API_BASE_URL": "http://13.207.85.164",
        }
    )

    assert settings.SOCKETIO_PATH == "/api/socket.io"


def test_production_standalone_keeps_root_socketio_path():
    from app.core.config import Settings

    settings = Settings.model_validate(
        {
            "ENVIRONMENT": "production",
            "DATABASE_URL": "postgresql+psycopg2://u:p@localhost/db",
            "SECRET_KEY": "test-secret-key",
            "GOOGLE_CLIENT_ID": "id",
            "GOOGLE_CLIENT_SECRET": "secret",
            "GOOGLE_REDIRECT_URI": "https://api.example.com/callback",
            "email_user": "test@example.com",
            "email_pass": "pass",
            "FRONTEND_URL": "https://app.example.com",
            "CORS_ORIGINS": "https://app.example.com",
            "SOCKETIO_PATH": "/socket.io",
            "SOCKETIO_STANDALONE": True,
            "PUBLIC_API_BASE_URL": "https://api.example.com",
        }
    )

    assert settings.SOCKETIO_PATH == "/socket.io"
