from datetime import datetime
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from app.models.chat_model import Conversation, Message
from app.repository.chat_repo import (
    DELETED_MESSAGE_PREVIEW,
    get_conversation_messages,
    message_list_preview,
    soft_delete_message,
    sync_conversation_last_message,
)
from app.services.chat_service import delete_message_service, get_messages_service


_CONV_ID = UUID("550e8400-e29b-41d4-a716-446655440001")
_MSG_ID = UUID("550e8400-e29b-41d4-a716-446655440010")
_USER_ID = UUID("550e8400-e29b-41d4-a716-446655440020")
_NOW = datetime.utcnow()


def test_message_list_preview_deleted():
    message = Message(content="secret text", is_deleted=True)
    assert message_list_preview(message) == DELETED_MESSAGE_PREVIEW


def test_message_list_preview_active():
    message = Message(content="Hello there", is_deleted=False)
    assert message_list_preview(message) == "Hello there"


@patch("app.repository.chat_repo.save_conversation")
@patch("app.repository.chat_repo.get_latest_conversation_message")
@patch("app.repository.chat_repo.get_conversation_by_id")
def test_sync_conversation_last_message_uses_deleted_preview(
    mock_get_conversation,
    mock_get_latest,
    mock_save,
):
    conversation = Conversation(id=_CONV_ID, last_message_preview="old text")
    deleted_latest = Message(
        id=_MSG_ID,
        conversation_id=_CONV_ID,
        content="old text",
        is_deleted=True,
        created_at=_NOW,
    )

    mock_get_conversation.return_value = conversation
    mock_get_latest.return_value = deleted_latest
    mock_save.side_effect = lambda _db, conv: conv

    result = sync_conversation_last_message(MagicMock(), _CONV_ID)

    assert result.last_message_preview == DELETED_MESSAGE_PREVIEW
    mock_save.assert_called_once()


@patch("app.services.chat_service.chat_repo.sync_conversation_last_message")
@patch("app.services.chat_service.chat_repo.soft_delete_message")
@patch("app.services.chat_service.chat_repo.get_message_by_id")
def test_delete_message_service_persists_and_syncs_preview(
    mock_get_message,
    mock_soft_delete,
    mock_sync_preview,
):
    message = Message(
        id=_MSG_ID,
        conversation_id=_CONV_ID,
        sender_id=_USER_ID,
        content="remove me",
        is_deleted=False,
    )
    deleted = Message(
        id=_MSG_ID,
        conversation_id=_CONV_ID,
        sender_id=_USER_ID,
        content="remove me",
        is_deleted=True,
        deleted_at=_NOW,
    )

    mock_get_message.return_value = message
    mock_soft_delete.return_value = deleted

    result = delete_message_service(
        MagicMock(),
        {"id": str(_USER_ID), "role": "provider"},
        _MSG_ID,
    )

    assert result.is_deleted is True
    mock_soft_delete.assert_called_once_with(mock_soft_delete.call_args[0][0], message)
    mock_sync_preview.assert_called_once_with(
        mock_sync_preview.call_args[0][0],
        _CONV_ID,
    )


@patch("app.services.chat_service.chat_repo.get_conversation_messages")
@patch("app.services.chat_service.chat_repo.is_participant")
def test_get_messages_service_includes_deleted_messages(mock_is_participant, mock_get_messages):
    deleted_message = Message(
        id=_MSG_ID,
        conversation_id=_CONV_ID,
        sender_id=_USER_ID,
        content="was here",
        message_type="text",
        is_deleted=True,
        created_at=_NOW,
    )

    mock_is_participant.return_value = True
    mock_get_messages.return_value = ([deleted_message], False, None)

    with patch(
        "app.services.chat_service.chat_repo.get_message_read_receipts",
        return_value=[],
    ):
        result = get_messages_service(
            MagicMock(),
            {"id": str(_USER_ID), "role": "provider"},
            _CONV_ID,
        )

    assert len(result.items) == 1
    assert result.items[0].is_deleted is True
    assert result.items[0].content == "was here"


def test_get_conversation_messages_query_does_not_filter_deleted():
    db = MagicMock()
    query = MagicMock()
    db.query.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit.return_value.all.return_value = []

    get_conversation_messages(db, _CONV_ID, limit=50)

    filter_args = query.filter.call_args[0]
    assert len(filter_args) == 1
    assert "is_deleted" not in str(filter_args[0])
