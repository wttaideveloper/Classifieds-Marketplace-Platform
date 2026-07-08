from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.socket_io import router
from app.core.dependencies import get_current_user

app = FastAPI()
app.include_router(router, prefix="/socket-io")

app.dependency_overrides[get_current_user] = lambda: {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "role": "customer",
    "email": "user@example.com",
}

client = TestClient(app)


def test_socket_events_catalog():
    response = client.get("/socket-io/events")
    assert response.status_code == 200
    body = response.json()
    assert len(body["client_events"]) == 6
    assert len(body["server_events"]) == 8
    assert "connection_url" in body
    assert "connection_path" in body
    assert "polling_test_url" in body
    assert body["connection_path"].startswith("/")


@patch("app.api.v1.endpoints.socket_io.validate_join_room")
def test_socket_join_room(mock_validate):
    mock_validate.return_value = {
        "conversation_id": "550e8400-e29b-41d4-a716-446655440001",
        "room": "conversation:550e8400-e29b-41d4-a716-446655440001",
        "authorized": True,
    }

    response = client.post(
        "/socket-io/join-room",
        json={"conversation_id": "550e8400-e29b-41d4-a716-446655440001"},
    )

    assert response.status_code == 200
    assert response.json()["socket_event"] == "join_room"


@patch("app.api.v1.endpoints.socket_io.emit_message_read", new_callable=AsyncMock)
@patch("app.api.v1.endpoints.socket_io.process_mark_read")
def test_socket_mark_read(mock_process, mock_emit):
    from uuid import UUID

    mock_process.return_value = (
        {"conversation_id": "550e8400-e29b-41d4-a716-446655440001", "read_at": "2026-07-07T12:00:00"},
        UUID("550e8400-e29b-41d4-a716-446655440001"),
    )

    response = client.post(
        "/socket-io/mark-read",
        json={"conversation_id": "550e8400-e29b-41d4-a716-446655440001"},
    )

    assert response.status_code == 200
    assert response.json()["socket_event"] == "mark_read"
    assert "message_read" in response.json()["server_events_emitted"]
