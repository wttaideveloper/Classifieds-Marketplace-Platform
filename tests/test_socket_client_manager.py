from unittest.mock import MagicMock, patch

import pytest

from app.realtime.client_manager import build_client_manager


@patch("app.realtime.client_manager.ping_redis", return_value=True)
@patch("app.realtime.client_manager.socketio.AsyncRedisManager")
def test_build_client_manager_with_redis_for_pubsub(mock_manager_cls, mock_ping):
    settings = MagicMock()
    settings.SOCKETIO_REDIS_URL = "redis://127.0.0.1:6379/0"

    with patch("app.realtime.client_manager.settings", settings):
        manager = build_client_manager()

    assert manager is mock_manager_cls.return_value
    mock_manager_cls.assert_called_once_with("redis://127.0.0.1:6379/0")


def test_build_client_manager_without_redis():
    settings = MagicMock()
    settings.SOCKETIO_REDIS_URL = ""

    with patch("app.realtime.client_manager.settings", settings):
        assert build_client_manager() is None


def test_combined_deploy_rejects_multiple_workers():
    from app.core.config import Settings

    with pytest.raises(ValueError, match="must be 1 for combined Socket.IO"):
        Settings.model_validate(
            {
                "ENVIRONMENT": "development",
                "DATABASE_URL": "postgresql+psycopg2://u:p@localhost/db",
                "SECRET_KEY": "test-secret-key",
                "GOOGLE_CLIENT_ID": "id",
                "GOOGLE_CLIENT_SECRET": "secret",
                "GOOGLE_REDIRECT_URI": "http://localhost/callback",
                "email_user": "test@example.com",
                "email_pass": "pass",
                "WEB_CONCURRENCY": 4,
                "SOCKET_WORKERS": 1,
            }
        )
