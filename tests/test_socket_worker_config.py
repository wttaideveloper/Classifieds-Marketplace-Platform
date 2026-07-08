import pytest

from app.core.config import Settings


def _base_settings(**overrides):
    data = {
        "ENVIRONMENT": "development",
        "DATABASE_URL": "postgresql+psycopg2://u:p@localhost/db",
        "SECRET_KEY": "test-secret-key",
        "GOOGLE_CLIENT_ID": "id",
        "GOOGLE_CLIENT_SECRET": "secret",
        "GOOGLE_REDIRECT_URI": "http://localhost/callback",
        "email_user": "test@example.com",
        "email_pass": "pass",
        "WEB_CONCURRENCY": 1,
    }
    data.update(overrides)
    return Settings.model_validate(data)


def test_multi_worker_requires_redis():
    with pytest.raises(ValueError, match="WEB_CONCURRENCY > 1 requires SOCKETIO_REDIS_URL"):
        _base_settings(WEB_CONCURRENCY=4)


def test_multi_worker_allowed_with_redis():
    settings = _base_settings(
        WEB_CONCURRENCY=4,
        SOCKETIO_REDIS_URL="redis://localhost:6379/0",
    )
    assert settings.WEB_CONCURRENCY == 4
    assert settings.SOCKETIO_REDIS_URL == "redis://localhost:6379/0"
