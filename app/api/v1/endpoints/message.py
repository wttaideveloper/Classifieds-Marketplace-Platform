from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, PaginatedResponse
from app.schemas.chat_schema import (
    CursorPaginatedResponse,
    MessageCreate,
    MessageDeleteResponse,
    MessageReadResponse,
    MessageReadStatusResponse,
    MessageResponse,
    MessageUpdate,
)
from app.services.chat_service import (
    delete_message_service,
    edit_message_service,
    get_messages_service,
    get_read_status_service,
    mark_conversation_read_service,
    mark_message_read_service,
    search_messages_service,
    send_message_service,
)

router = APIRouter(tags=["Messages"])


@router.post(
    "/",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Send Message",
    description=(
        "Send a text or attachment message. Validates conversation status, "
        "read-only mode, expiration, and subscription limits for preview chats."
    ),
)
def send_message(
    payload: MessageCreate = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return send_message_service(db, current_user, payload)


@router.get(
    "/search",
    response_model=PaginatedResponse[MessageResponse],
    summary="Search Messages",
)
def search_messages(
    q: str = Query(..., min_length=1, description="Search query."),
    conversation_id: UUID | None = Query(None),
    provider_id: UUID | None = Query(None),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return search_messages_service(
        db,
        current_user,
        search=q,
        conversation_id=conversation_id,
        provider_id=provider_id,
        page=page,
        page_size=page_size,
    )


@router.patch(
    "/{message_id}/read",
    response_model=MessageReadResponse,
    summary="Mark Message as Read",
)
def mark_message_read(
    message_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return mark_message_read_service(db, current_user, message_id)


@router.get(
    "/{message_id}/read-status",
    response_model=MessageReadStatusResponse,
    summary="Get Message Read Status",
)
def get_read_status(
    message_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return get_read_status_service(db, current_user, message_id)


@router.patch(
    "/{message_id}",
    response_model=MessageResponse,
    summary="Edit Message",
    description=(
        "Edit a text message. Only the sender or admin can edit. "
        "Deleted messages cannot be edited. If the edited message is the latest, "
        "conversation last_message_preview is updated."
    ),
)
def edit_message(
    message_id: UUID = Path(...),
    payload: MessageUpdate = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return edit_message_service(db, current_user, message_id, payload.content)


@router.delete(
    "/{message_id}",
    response_model=MessageDeleteResponse,
    summary="Delete Message",
    description=(
        "Soft-delete a message (sets is_deleted=true). Deleted messages remain in "
        "GET /conversations/{id}/messages with is_deleted=true. If the deleted message "
        "was the latest, conversation last_message_preview becomes "
        "'deleted this message'. Only the sender or admin can delete."
    ),
)
def delete_message(
    message_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return delete_message_service(db, current_user, message_id)
