from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.schemas.chat_schema import (
    AttachmentDeleteResponse,
    AttachmentResponse,
    CursorPaginatedResponse,
    MessageResponse,
)
from app.services.chat_attachment_service import (
    delete_attachment_service,
    download_attachment_service,
    get_attachment_service,
    upload_attachment_service,
)
from app.services.chat_service import get_messages_service, mark_conversation_read_service

router = APIRouter(tags=["Messages"])


@router.get(
    "/{conversation_id}/messages",
    response_model=CursorPaginatedResponse[MessageResponse],
    summary="Get Conversation Messages",
    description="Load messages with cursor-based pagination. Use next_cursor to load older messages.",
)
def get_conversation_messages(
    conversation_id: UUID = Path(...),
    cursor: str | None = Query(None, description="Cursor from previous response for older messages."),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return get_messages_service(
        db, current_user, conversation_id, cursor=cursor, limit=limit
    )


@router.patch(
    "/{conversation_id}/read",
    summary="Mark Conversation as Read",
)
def mark_conversation_read(
    conversation_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return mark_conversation_read_service(db, current_user, conversation_id)
