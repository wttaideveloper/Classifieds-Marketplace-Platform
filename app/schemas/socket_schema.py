from uuid import UUID

from typing import Any
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.chat_schema import MessageType


class SocketConversationRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {"conversation_id": "550e8400-e29b-41d4-a716-446655440000"}
        }
    )

    conversation_id: UUID = Field(..., description="Conversation to join or leave.")


class SocketJoinRoomResponse(BaseModel):
    socket_event: str = "join_room"
    conversation_id: str
    room: str = Field(..., description="Socket.IO room name used by join_room.")
    authorized: bool = True
    note: str = (
        "Authorization verified. Connect via Socket.IO using the path from "
        "GET /api/v1/socket-io/events (`connection_path`) and emit `join_room`."
    )


class SocketLeaveRoomResponse(BaseModel):
    socket_event: str = "leave_room"
    conversation_id: str
    room: str
    note: str = (
        "Connect via Socket.IO and emit `leave_room` with the same payload "
        "to leave the live room."
    )


class SocketSendMessageRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
                "content": "Hello via Socket.IO",
                "message_type": "text",
            }
        }
    )

    conversation_id: UUID
    content: str | None = None
    message_type: MessageType = "text"
    attachment_id: UUID | None = None


class SocketTypingRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {"conversation_id": "550e8400-e29b-41d4-a716-446655440000"}
        }
    )

    conversation_id: UUID


class SocketMarkReadRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {"message_id": "550e8400-e29b-41d4-a716-446655440002"}
        }
    )

    message_id: UUID | None = Field(None, description="Mark a single message as read.")
    conversation_id: UUID | None = Field(None, description="Mark entire conversation as read.")


class SocketEventResult(BaseModel):
    socket_event: str = Field(..., description="Client event that was executed.")
    data: dict = Field(default_factory=dict, description="Event result payload.")
    server_events_emitted: list[str] = Field(
        default_factory=list,
        description="Server events broadcast to connected Socket.IO clients.",
    )


class SocketAuthInfo(BaseModel):
    type: str = Field("JWT Bearer", description="Authentication method.")
    connect: str = Field(
        ...,
        description="Example socket.io-client connect call.",
        examples=[
            'io("http://13.207.85.164", { path: "/api/socket.io", auth: { token: "<JWT>" } })'
        ],
    )


class ServerEventSchema(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "event": "join_room",
                    "description": "Join a conversation room for real-time updates.",
                    "payload": {"conversation_id": "uuid"},
                },
                {
                    "event": "new_message",
                    "description": "Broadcast when a new message is sent.",
                    "payload": {"conversation_id": "uuid", "message": "{...}"},
                },
            ]
        }
    )

    event: str = Field(..., description="Socket.IO event name.", examples=["join_room", "new_message"])
    description: str = Field(..., description="What the event does.")
    payload: dict[str, Any] = Field(
        ...,
        description="Payload field names with value types or examples.",
        json_schema_extra={
            "example": {
                "conversation_id": "uuid",
                "content": "string",
                "message_type": "text|image|document|audio|video",
            }
        },
    )


CLIENT_EVENTS_CATALOG = [
    ServerEventSchema(
        event="join_room",
        description="Join a conversation room for real-time updates.",
        payload={"conversation_id": "uuid"},
    ),
    ServerEventSchema(
        event="leave_room",
        description="Leave a conversation room.",
        payload={"conversation_id": "uuid"},
    ),
    ServerEventSchema(
        event="send_message",
        description="Send a message and broadcast to room participants.",
        payload={
            "conversation_id": "uuid",
            "content": "string",
            "message_type": "text|image|document|audio|video",
            "attachment_id": "uuid (optional)",
        },
    ),
    ServerEventSchema(
        event="typing_start",
        description="Signal that the user started typing.",
        payload={"conversation_id": "uuid"},
    ),
    ServerEventSchema(
        event="typing_stop",
        description="Signal that the user stopped typing.",
        payload={"conversation_id": "uuid"},
    ),
    ServerEventSchema(
        event="mark_read",
        description="Mark a message or entire conversation as read.",
        payload={"message_id": "uuid (optional)", "conversation_id": "uuid (optional)"},
    ),
]

SERVER_EVENTS_CATALOG = [
    ServerEventSchema(
        event="new_message",
        description="Broadcast when a new message is sent.",
        payload={"conversation_id": "uuid", "message": "{...}"},
    ),
    ServerEventSchema(
        event="message_read",
        description="Broadcast when messages are marked read.",
        payload={"conversation_id": "uuid", "message_id": "uuid", "user_id": "uuid", "read_at": "ISO datetime"},
    ),
    ServerEventSchema(
        event="typing",
        description="Broadcast typing indicator changes.",
        payload={"conversation_id": "uuid", "user_id": "uuid", "is_typing": True},
    ),
    ServerEventSchema(
        event="conversation_updated",
        description="Broadcast when conversation metadata changes.",
        payload={"conversation_id": "uuid", "conversation": "{...}"},
    ),
    ServerEventSchema(
        event="user_online",
        description="Broadcast when a user connects.",
        payload={"user_id": "uuid", "status": "online"},
    ),
    ServerEventSchema(
        event="user_offline",
        description="Broadcast when a user disconnects.",
        payload={"user_id": "uuid", "status": "offline"},
    ),
    ServerEventSchema(
        event="notification",
        description="Sent to user room for new message alerts.",
        payload={"type": "new_message", "conversation_id": "uuid", "title": "string", "body": "string", "message": "{...}"},
    ),
    ServerEventSchema(
        event="error",
        description="Sent to the requesting client on validation or auth errors.",
        payload={"event": "event_name", "detail": "error message"},
    ),
]


def _catalog_to_dict(items: list[ServerEventSchema]) -> list[dict[str, Any]]:
    return [item.model_dump() for item in items]


def build_events_catalog_swagger_example() -> dict[str, Any]:
    return {
        "connection_url": "http://13.207.85.164",
        "connection_path": "/api/socket.io",
        "polling_test_url": "http://13.207.85.164/api/socket.io?EIO=4&transport=polling",
        "auth": {
            "type": "JWT Bearer",
            "connect": 'io("http://13.207.85.164", { path: "/api/socket.io", auth: { token: "<JWT>" } })',
        },
        "deployment_notes": [
            "Socket.IO is mounted on app.main:socket_app.",
            "Configured SOCKETIO_PATH=/api/socket.io",
            "Frontend: io(baseUrl, { path: '/api/socket.io', auth: { token } })",
        ],
        "client_events": _catalog_to_dict(CLIENT_EVENTS_CATALOG),
        "server_events": _catalog_to_dict(SERVER_EVENTS_CATALOG),
    }


class ServerEventsCatalogResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"example": build_events_catalog_swagger_example()}
    )

    connection_url: str = Field(
        ...,
        description="Public base URL for Socket.IO (no trailing slash).",
        examples=["http://13.207.85.164"],
    )
    connection_path: str = Field(
        ...,
        description="Socket.IO path passed to the client `path` option.",
        examples=["/api/socket.io"],
    )
    polling_test_url: str = Field(
        ...,
        description="Engine.IO polling probe URL for deployment verification.",
        examples=["http://13.207.85.164/api/socket.io?EIO=4&transport=polling"],
    )
    auth: SocketAuthInfo = Field(
        ...,
        description="How to authenticate the Socket.IO connection.",
    )
    deployment_notes: list[str] = Field(default_factory=list)
    client_events: list[ServerEventSchema] = Field(
        ...,
        description="Events the browser/client emits to the server.",
    )
    server_events: list[ServerEventSchema] = Field(
        ...,
        description="Events the server emits to connected clients.",
    )
