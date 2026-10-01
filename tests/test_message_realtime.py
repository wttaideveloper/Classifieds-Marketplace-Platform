"""Realtime coverage for message edit/delete.

REST PATCH/DELETE /messages/{id} are what CM_Web and CM_mobile actually call
(there is no client-invocable Socket.IO edit/delete event), so the
`message_updated`/`message_deleted` broadcasts are wired in at that layer,
right after the existing chat_service edit/delete calls succeed — mirroring
how `user_online`/`user_offline` are server-only broadcasts triggered by a
non-socket-event source (connect/disconnect) rather than a client event.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.v1.endpoints.message import router
from app.core.dependencies import get_current_user
from app.realtime.rooms import conversation_room
from app.realtime.emitters import emit_message_deleted, emit_message_updated
from app.schemas.chat_schema import MessageDeleteResponse

CONV_ID = UUID("550e8400-e29b-41d4-a716-446655440001")
OTHER_CONV_ID = UUID("550e8400-e29b-41d4-a716-446655440099")
MSG_ID = UUID("550e8400-e29b-41d4-a716-446655440010")
USER_ID = "550e8400-e29b-41d4-a716-446655440020"

app = FastAPI()
app.include_router(router, prefix="/messages")
app.dependency_overrides[get_current_user] = lambda: {
    "id": USER_ID,
    "role": "customer",
    "email": "user@example.com",
}

client = TestClient(app)


def _message_dict(**overrides):
    data = {
        "id": MSG_ID,
        "conversation_id": CONV_ID,
        "sender_id": USER_ID,
        "content": "edited text",
        "message_type": "text",
        "attachment_id": None,
        "is_deleted": False,
        "is_edited": True,
        "edited_at": "2026-01-01T00:00:00",
        "created_at": "2026-01-01T00:00:00",
        "read_by": [],
    }
    data.update(overrides)
    return data


# --- Edit: success emits exactly once, after the DB update ---------------------

@patch("app.api.v1.endpoints.message.emit_message_updated", new_callable=AsyncMock)
@patch("app.api.v1.endpoints.message.edit_message_service")
def test_edit_message_emits_message_updated_after_successful_edit(mock_service, mock_emit):
    mock_service.return_value = _message_dict()

    response = client.patch(f"/messages/{MSG_ID}", json={"content": "edited text"})

    assert response.status_code == 200
    mock_service.assert_called_once()
    mock_emit.assert_awaited_once_with(CONV_ID, _message_dict())


@patch("app.api.v1.endpoints.message.emit_message_updated", new_callable=AsyncMock)
@patch("app.api.v1.endpoints.message.edit_message_service")
def test_edit_message_does_not_emit_when_unauthorized(mock_service, mock_emit):
    mock_service.side_effect = HTTPException(status_code=403, detail="Not authorized to edit this message")

    response = client.patch(f"/messages/{MSG_ID}", json={"content": "hacked"})

    assert response.status_code == 403
    mock_emit.assert_not_called()


@patch("app.api.v1.endpoints.message.emit_message_updated", new_callable=AsyncMock)
@patch("app.api.v1.endpoints.message.edit_message_service")
def test_edit_message_does_not_emit_when_db_update_fails(mock_service, mock_emit):
    mock_service.side_effect = RuntimeError("db commit failed")
    no_raise_client = TestClient(app, raise_server_exceptions=False)

    response = no_raise_client.patch(f"/messages/{MSG_ID}", json={"content": "edited text"})

    assert response.status_code == 500
    mock_emit.assert_not_called()


# --- Delete: success emits exactly once, after the DB update -------------------

@patch("app.api.v1.endpoints.message.emit_message_deleted", new_callable=AsyncMock)
@patch("app.api.v1.endpoints.message.delete_message_service")
def test_delete_message_emits_message_deleted_after_successful_delete(mock_service, mock_emit):
    mock_service.return_value = MessageDeleteResponse(
        id=MSG_ID,
        conversation_id=CONV_ID,
        is_deleted=True,
        deleted_at="2026-01-01T00:00:00",
    )

    response = client.delete(f"/messages/{MSG_ID}")

    assert response.status_code == 200
    mock_service.assert_called_once()
    mock_emit.assert_awaited_once_with(CONV_ID, MSG_ID)


@patch("app.api.v1.endpoints.message.emit_message_deleted", new_callable=AsyncMock)
@patch("app.api.v1.endpoints.message.delete_message_service")
def test_delete_message_does_not_emit_when_unauthorized(mock_service, mock_emit):
    mock_service.side_effect = HTTPException(status_code=403, detail="Not authorized to delete this message")

    response = client.delete(f"/messages/{MSG_ID}")

    assert response.status_code == 403
    mock_emit.assert_not_called()


@patch("app.api.v1.endpoints.message.emit_message_deleted", new_callable=AsyncMock)
@patch("app.api.v1.endpoints.message.delete_message_service")
def test_delete_message_does_not_emit_when_db_delete_fails(mock_service, mock_emit):
    mock_service.side_effect = RuntimeError("db commit failed")
    no_raise_client = TestClient(app, raise_server_exceptions=False)

    response = no_raise_client.delete(f"/messages/{MSG_ID}")

    assert response.status_code == 500
    mock_emit.assert_not_called()


# --- Emitter-level: correct event name, room, and payload shape ----------------

def test_emit_message_updated_targets_only_its_own_conversation_room():
    mock_sio_emit = AsyncMock()
    with patch("app.realtime.emitters.sio.emit", mock_sio_emit):
        asyncio.run(emit_message_updated(CONV_ID, _message_dict()))

    mock_sio_emit.assert_awaited_once()
    args, kwargs = mock_sio_emit.call_args
    assert args[0] == "message_updated"
    assert kwargs["room"] == conversation_room(CONV_ID) == f"conversation:{CONV_ID}"
    assert kwargs["room"] != conversation_room(OTHER_CONV_ID)
    payload = args[1]
    assert payload["conversation_id"] == str(CONV_ID)
    assert payload["message"]["id"] == str(MSG_ID)
    assert payload["message"]["content"] == "edited text"


def test_emit_message_deleted_targets_only_its_own_conversation_room():
    mock_sio_emit = AsyncMock()
    with patch("app.realtime.emitters.sio.emit", mock_sio_emit):
        asyncio.run(emit_message_deleted(CONV_ID, MSG_ID))

    mock_sio_emit.assert_awaited_once()
    args, kwargs = mock_sio_emit.call_args
    assert args[0] == "message_deleted"
    assert kwargs["room"] == conversation_room(CONV_ID)
    payload = args[1]
    assert payload == {"conversation_id": str(CONV_ID), "message_id": str(MSG_ID)}


def test_emit_functions_call_sio_exactly_once_per_action():
    """One backend emit call is enough — Socket.IO's own room broadcast fans
    it out to every socket in the room (including a user's multiple
    devices), so the server never needs to loop over sockets itself."""
    mock_sio_emit = AsyncMock()
    with patch("app.realtime.emitters.sio.emit", mock_sio_emit):
        asyncio.run(emit_message_updated(CONV_ID, _message_dict()))
        asyncio.run(emit_message_deleted(CONV_ID, MSG_ID))

    assert mock_sio_emit.await_count == 2
