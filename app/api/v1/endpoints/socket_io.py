from uuid import UUID

from fastapi import APIRouter, Body, Depends, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.realtime.emitters import (
    emit_conversation_updated,
    emit_message_read,
    emit_new_message,
    emit_typing,
    notify_participants_new_message,
)
from app.schemas.chat_schema import MessageCreate
from app.schemas.socket_schema import (
    CLIENT_EVENTS_CATALOG,
    SERVER_EVENTS_CATALOG,
    ServerEventsCatalogResponse,
    SocketConversationRequest,
    SocketEventResult,
    SocketJoinRoomResponse,
    SocketLeaveRoomResponse,
    SocketMarkReadRequest,
    SocketSendMessageRequest,
    SocketTypingRequest,
)
from app.services.socket_connection_service import build_socket_connection_info
from app.services.socket_io_service import (
    process_mark_read,
    process_send_message,
    process_typing,
    validate_join_room,
    validate_leave_room,
)

router = APIRouter(tags=["Socket.IO"])


@router.get(
    "/connection-info",
    summary="Socket.IO connection info",
    description="Deployment diagnostics: base URL, path, and polling probe URL.",
)
def get_socket_connection_info():
    return build_socket_connection_info()


@router.get(
    "/events",
    response_model=ServerEventsCatalogResponse,
    summary="Socket.IO Events Catalog",
    description="Full reference of client and server Socket.IO events with payload shapes.",
)
def get_socket_events_catalog():
    connection = build_socket_connection_info()
    return ServerEventsCatalogResponse(
        connection_url=connection["connection_url"],
        connection_path=connection["connection_path"],
        polling_test_url=connection["polling_test_url"],
        auth=connection["auth"],
        deployment_notes=connection["deployment_notes"],
        client_events=CLIENT_EVENTS_CATALOG,
        server_events=SERVER_EVENTS_CATALOG,
    )


@router.post(
    "/join-room",
    response_model=SocketJoinRoomResponse,
    summary="join_room (Socket.IO client event)",
    description=(
        "Swagger-interactive mirror of the Socket.IO `join_room` event. "
        "Validates participant access and returns the room name. "
        "For live updates, connect via Socket.IO and emit `join_room`."
    ),
)
def socket_join_room(
    payload: SocketConversationRequest = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    result = validate_join_room(db, current_user, payload.conversation_id)
    return SocketJoinRoomResponse(**result)


@router.post(
    "/leave-room",
    response_model=SocketLeaveRoomResponse,
    summary="leave_room (Socket.IO client event)",
    description="Swagger-interactive mirror of the Socket.IO `leave_room` event.",
)
def socket_leave_room(
    payload: SocketConversationRequest = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    result = validate_leave_room(db, current_user, payload.conversation_id)
    return SocketLeaveRoomResponse(**result)


@router.post(
    "/send-message",
    response_model=SocketEventResult,
    summary="send_message (Socket.IO client event)",
    description=(
        "Execute the `send_message` Socket.IO event via REST. "
        "Persists the message and broadcasts `new_message`, `conversation_updated`, "
        "and `notification` to connected Socket.IO clients."
    ),
)
async def socket_send_message(
    payload: SocketSendMessageRequest = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    message_data, conversation_data = process_send_message(
        db,
        current_user,
        MessageCreate(
            conversation_id=payload.conversation_id,
            content=payload.content,
            message_type=payload.message_type,
            attachment_id=payload.attachment_id,
        ),
    )

    await emit_new_message(payload.conversation_id, message_data)
    await emit_conversation_updated(payload.conversation_id, conversation_data)
    await notify_participants_new_message(
        db,
        payload.conversation_id,
        str(current_user["id"]),
        message_data,
        conversation_data.get("last_message_preview") or "",
    )

    return SocketEventResult(
        socket_event="send_message",
        data={"message": message_data, "conversation": conversation_data},
        server_events_emitted=["new_message", "conversation_updated", "notification"],
    )


@router.post(
    "/typing-start",
    response_model=SocketEventResult,
    summary="typing_start (Socket.IO client event)",
    description="Execute `typing_start` and broadcast the `typing` server event.",
)
async def socket_typing_start(
    payload: SocketTypingRequest = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    typing_data = process_typing(db, current_user, payload.conversation_id, True)
    await emit_typing(payload.conversation_id, current_user["id"], True)
    return SocketEventResult(
        socket_event="typing_start",
        data=typing_data,
        server_events_emitted=["typing"],
    )


@router.post(
    "/typing-stop",
    response_model=SocketEventResult,
    summary="typing_stop (Socket.IO client event)",
    description="Execute `typing_stop` and broadcast the `typing` server event.",
)
async def socket_typing_stop(
    payload: SocketTypingRequest = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    typing_data = process_typing(db, current_user, payload.conversation_id, False)
    await emit_typing(payload.conversation_id, current_user["id"], False)
    return SocketEventResult(
        socket_event="typing_stop",
        data=typing_data,
        server_events_emitted=["typing"],
    )


@router.post(
    "/mark-read",
    response_model=SocketEventResult,
    summary="mark_read (Socket.IO client event)",
    description="Execute `mark_read` and broadcast the `message_read` server event.",
)
async def socket_mark_read(
    payload: SocketMarkReadRequest = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    read_data, conv_id = process_mark_read(
        db,
        current_user,
        message_id=payload.message_id,
        conversation_id=payload.conversation_id,
    )
    await emit_message_read(conv_id, read_data)
    return SocketEventResult(
        socket_event="mark_read",
        data=read_data,
        server_events_emitted=["message_read"],
    )
