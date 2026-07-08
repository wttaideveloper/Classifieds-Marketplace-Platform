from datetime import datetime
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from app.models.chat_model import Conversation, Message
from app.repository.chat_repo import (
    DELETED_MESSAGE_PREVIEW,
    message_list_preview,
    resolve_conversation_preview,
)
from app.services.chat_service import delete_message_service, edit_message_service


_CONV_ID = UUID("550e8400-e29b-41d4-a716-446655440001")
_MSG_ID = UUID("550e8400-e29b-41d4-a716-446655440010")
_USER_ID = UUID("550e8400-e29b-41d4-a716-446655440020")
_NOW = datetime.utcnow()


def test_message_list_preview_deleted():
    message = Message(content="secret text", is_deleted=True)
    assert message_list_preview(message) == DELETED_MESSAGE_PREVIEW
    assert DELETED_MESSAGE_PREVIEW == "deleted this message"


@patch("app.repository.chat_repo.get_latest_conversation_message")
def test_resolve_conversation_preview_deleted_latest(mock_get_latest):
    conversation = Conversation(id=_CONV_ID, last_message_preview="stale old content")
    mock_get_latest.return_value = Message(
        id=_MSG_ID,
        conversation_id=_CONV_ID,
        content="stale old content",
        is_deleted=True,
        created_at=_NOW,
    )

    preview, _at = resolve_conversation_preview(MagicMock(), conversation)

    assert preview == "deleted this message"


@patch("app.services.chat_service.chat_repo.sync_conversation_last_message")
@patch("app.services.chat_service.chat_repo.update_message")
@patch("app.services.chat_service.chat_repo.get_latest_conversation_message")
@patch("app.services.chat_service.chat_repo.get_message_by_id")
def test_edit_message_service(
    mock_get_message,
    mock_get_latest,
    mock_update,
    mock_sync,
):
    message = Message(
        id=_MSG_ID,
        conversation_id=_CONV_ID,
        sender_id=_USER_ID,
        content="old",
        message_type="text",
        is_deleted=False,
        is_edited=False,
    )
    updated = Message(
        id=_MSG_ID,
        conversation_id=_CONV_ID,
        sender_id=_USER_ID,
        content="new text",
        message_type="text",
        is_deleted=False,
        is_edited=True,
        edited_at=_NOW,
        created_at=_NOW,
    )

    mock_get_message.return_value = message
    mock_update.return_value = updated
    mock_get_latest.return_value = updated

    with patch(
        "app.services.chat_service.chat_repo.get_message_read_receipts",
        return_value=[],
    ):
        result = edit_message_service(
            MagicMock(),
            {"id": str(_USER_ID), "role": "provider"},
            _MSG_ID,
            "new text",
        )

    assert result["content"] == "new text"
    assert result["is_edited"] is True
    mock_sync.assert_called_once()
