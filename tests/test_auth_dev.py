from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.socket_io import router
from app.core.config import settings
from app.core.dependencies import get_current_user

app = FastAPI()
app.include_router(router, prefix="/socket-io")
client = TestClient(app)


def test_dev_user_dependency_directly():
    user = get_current_user(credentials=None)
    assert user["id"] == settings.DEV_DEFAULT_USER_ID
    assert user["email"] == "dev@localhost"


def test_no_token_uses_dev_user_in_development():
    assert not settings.is_production

    captured = {}

    def capture_user():
        user = get_current_user(credentials=None)
        captured["user"] = user
        return user

    app.dependency_overrides[get_current_user] = capture_user

    with patch(
        "app.api.v1.endpoints.socket_io.validate_join_room",
        return_value={
            "conversation_id": "550e8400-e29b-41d4-a716-446655440001",
            "room": "conversation:550e8400-e29b-41d4-a716-446655440001",
            "authorized": True,
        },
    ):
        response = client.post(
            "/socket-io/join-room",
            json={"conversation_id": "550e8400-e29b-41d4-a716-446655440001"},
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert captured["user"]["id"] == settings.DEV_DEFAULT_USER_ID


def test_get_dev_user_from_realtime_auth():
    from app.realtime.auth import get_dev_user

    user = get_dev_user()
    assert user["id"] == settings.DEV_DEFAULT_USER_ID
