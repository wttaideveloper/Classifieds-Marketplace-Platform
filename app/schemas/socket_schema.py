from datetime import datetime
from uuid import UUID

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
        "Authorization verified. Connect via Socket.IO at /socket.io and emit "
        "`join_room` with the same payload to join the live room."
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


class ServerEventSchema(BaseModel):
    event: str
    description: str
    payload: dict = Field(default_factory=dict)


class ServerEventsCatalogResponse(BaseModel):
    connection_url: str = "/socket.io"
    auth: dict = Field(
        default_factory=lambda: {
            "type": "JWT Bearer",
            "connect": 'io(url, { auth: { token: "<JWT access token>" } })',
        }
    )
    client_events: list[ServerEventSchema]
    server_events: list[ServerEventSchema]


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
