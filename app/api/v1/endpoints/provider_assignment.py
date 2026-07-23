from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.schemas.chat_schema import (
    ProviderAssignRequest,
    ProviderAssignmentResponse,
    ProviderReassignRequest,
)
from app.services.chat_service import assign_provider_service, get_assigned_provider_service

router = APIRouter(tags=["Provider Assignment"])


@router.post(
    "/assign",
    response_model=ProviderAssignmentResponse,
    summary="Assign Provider",
)
def assign_provider(
    payload: ProviderAssignRequest = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return assign_provider_service(
        db, current_user, payload.conversation_id, payload.provider_id
    )


@router.post(
    "/reassign",
    response_model=ProviderAssignmentResponse,
    summary="Reassign Provider",
)
def reassign_provider(
    payload: ProviderReassignRequest = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return assign_provider_service(
        db, current_user, payload.conversation_id, payload.provider_id
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=ProviderAssignmentResponse,
    summary="Get Assigned Provider",
)
def get_assigned_provider(
    conversation_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return get_assigned_provider_service(db, current_user, conversation_id)
