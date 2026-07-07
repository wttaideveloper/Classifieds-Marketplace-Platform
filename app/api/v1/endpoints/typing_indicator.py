from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.schemas.chat_schema import TypingIndicatorResponse, TypingUpdateRequest
from app.services.chat_service import get_typing_service, update_typing_service

router = APIRouter(tags=["Typing Indicator"])


@router.put(
    "/{conversation_id}/typing",
    response_model=TypingIndicatorResponse,
    summary="Update Typing Indicator",
    description=(
        "REST endpoint for typing status. Prefer Socket.IO events "
        "`typing_start` and `typing_stop` for real-time delivery."
    ),
)
def update_typing(
    conversation_id: UUID = Path(...),
    payload: TypingUpdateRequest = Body(default_factory=TypingUpdateRequest),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return update_typing_service(db, current_user, conversation_id, payload.is_typing)


@router.get(
    "/{conversation_id}/typing",
    response_model=list[TypingIndicatorResponse],
    summary="Get Typing Users",
)
def get_typing_users(
    conversation_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return get_typing_service(db, current_user, conversation_id)
