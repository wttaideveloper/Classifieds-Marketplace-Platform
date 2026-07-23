from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.conversation import router as conversation_router
from app.api.v1.endpoints.message import router as message_router
from app.api.v1.endpoints.chat_subscription import router as subscription_router
from app.core.dependencies import get_current_user

app = FastAPI()
app.include_router(conversation_router, prefix="/conversations")
app.include_router(message_router, prefix="/messages")
app.include_router(subscription_router, prefix="/subscriptions")

_USER_ID = "550e8400-e29b-41d4-a716-446655440000"
_CONV_ID = "550e8400-e29b-41d4-a716-446655440001"
_NOW = datetime.utcnow().isoformat() + "Z"

app.dependency_overrides[get_current_user] = lambda: {
    "id": _USER_ID,
    "role": "customer",
    "email": "user@example.com",
}

client = TestClient(app)


@patch("app.api.v1.endpoints.conversation.list_conversations_service")
def test_list_archived_conversations(mock_service):
    mock_service.return_value = {
        "items": [],
        "pagination": {"total": 0, "page": 1, "page_size": 20, "total_pages": 0},
    }

    response = client.get("/conversations/archived")

    assert response.status_code == 200
    mock_service.assert_called_once()
    assert mock_service.call_args.kwargs["status_filter"] == "archived"


@patch("app.api.v1.endpoints.conversation.list_provider_conversations_service")
def test_list_provider_archived_conversations(mock_service):
    mock_service.return_value = {
        "items": [],
        "pagination": {"total": 0, "page": 1, "page_size": 20, "total_pages": 0},
    }

    response = client.get("/conversations/provider/archived")

    assert response.status_code == 200
    mock_service.assert_called_once()
    assert mock_service.call_args.kwargs["status_filter"] == "archived"


@patch("app.api.v1.endpoints.conversation.archive_conversation_service")
def test_archive_conversation(mock_service):
    mock_service.return_value = {
        "id": _CONV_ID,
        "status": "archived",
        "is_archived": True,
        "archived_at": _NOW,
        "updated_at": _NOW,
    }

    response = client.patch(
        f"/conversations/{_CONV_ID}/archive",
        json={"archived": True},
    )

    assert response.status_code == 200
    assert response.json()["is_archived"] is True
    assert response.json()["status"] == "archived"
    mock_service.assert_called_once()
    assert mock_service.call_args.kwargs["archived"] is True


@patch("app.api.v1.endpoints.conversation.archive_conversation_service")
def test_unarchive_conversation(mock_service):
    mock_service.return_value = {
        "id": _CONV_ID,
        "status": "open",
        "is_archived": False,
        "archived_at": None,
        "updated_at": _NOW,
    }

    response = client.patch(
        f"/conversations/{_CONV_ID}/archive",
        json={"archived": False},
    )

    assert response.status_code == 200
    assert response.json()["is_archived"] is False
    assert response.json()["status"] == "open"
    assert response.json()["archived_at"] is None
    mock_service.assert_called_once()
    assert mock_service.call_args.kwargs["archived"] is False


@patch("app.api.v1.endpoints.conversation.create_conversation_service")
def test_create_conversation(mock_service):
    mock_service.return_value = {
        "id": _CONV_ID,
        "tenant_id": None,
        "status": "open",
        "conversation_type": "standard",
        "is_read_only": False,
        "subject": "Hello",
        "context_type": None,
        "context_id": None,
        "assigned_provider_id": None,
        "expires_at": None,
        "last_message_at": None,
        "last_message_preview": None,
        "created_by": _USER_ID,
        "unread_count": 0,
        "participants": [],
        "created_at": _NOW,
        "updated_at": _NOW,
    }

    response = client.post(
        "/conversations/",
        json={"subject": "Hello", "participant_ids": []},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "open"


@patch("app.api.v1.endpoints.conversation.list_conversations_service")
def test_list_conversations(mock_service):
    mock_service.return_value = {
        "items": [],
        "pagination": {"total": 0, "page": 1, "page_size": 20, "total_pages": 0},
    }

    response = client.get("/conversations/")

    assert response.status_code == 200
    assert response.json()["pagination"]["total"] == 0


@patch("app.api.v1.endpoints.message.send_message_service")
def test_send_message(mock_service):
    mock_service.return_value = {
        "id": str(uuid4()),
        "conversation_id": _CONV_ID,
        "sender_id": _USER_ID,
        "content": "Hello",
        "message_type": "text",
        "attachment_id": None,
        "is_deleted": False,
        "created_at": _NOW,
        "read_by": [],
    }

    response = client.post(
        "/messages/",
        json={"conversation_id": _CONV_ID, "content": "Hello", "message_type": "text"},
    )

    assert response.status_code == 201
    assert response.json()["content"] == "Hello"


@patch("app.api.v1.endpoints.chat_subscription.chat_eligibility_service")
def test_chat_eligibility(mock_service):
    mock_service.return_value = {
        "eligible": True,
        "plan_type": "free",
        "reason": None,
        "remaining_messages": 10,
        "monthly_limit": 10,
        "messages_used": 0,
        "period_end": "2026-07-31",
    }

    response = client.get("/subscriptions/chat-eligibility")

    assert response.status_code == 200
    assert response.json()["eligible"] is True
