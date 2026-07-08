from unittest.mock import MagicMock, patch

import pytest

from app.realtime.client_manager import build_client_manager, ping_redis


@patch("app.realtime.client_manager.ping_redis", return_value=True)
@patch("app.realtime.client_manager.socketio.AsyncRedisManager")
def test_build_client_manager_with_redis(mock_manager_cls, mock_ping):
    settings = MagicMock()
    settings.SOCKETIO_REDIS_URL = "redis://127.0.0.1:6379/0"
    settings.WEB_CONCURRENCY = 4

    with patch("app.realtime.client_manager.settings", settings):
        manager = build_client_manager()

    assert manager is mock_manager_cls.return_value
    mock_manager_cls.assert_called_once_with("redis://127.0.0.1:6379/0")
    mock_ping.assert_called_once_with("redis://127.0.0.1:6379/0")


def test_build_client_manager_requires_redis_for_multi_worker():
    settings = MagicMock()
    settings.SOCKETIO_REDIS_URL = ""
    settings.WEB_CONCURRENCY = 4

    with patch("app.realtime.client_manager.settings", settings):
        with pytest.raises(RuntimeError, match="WEB_CONCURRENCY > 1 requires SOCKETIO_REDIS_URL"):
            build_client_manager()


@patch("app.realtime.client_manager.redis.from_url")
def test_ping_redis_success(mock_from_url):
    mock_from_url.return_value.ping.return_value = True
    assert ping_redis("redis://127.0.0.1:6379/0") is True


@patch("app.realtime.client_manager.redis.from_url", side_effect=ConnectionError("down"))
def test_ping_redis_failure(_mock_from_url):
    assert ping_redis("redis://127.0.0.1:6379/0") is False
