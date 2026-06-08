from fastapi import APIRouter, Depends, status
from typing import List
from sqlalchemy.orm import Session
from uuid import UUID
from app.db.database import get_db

from app.schemas.meeting_schema import (
    MeetingConnectRequest,
    MeetingConnectResponse,
    CreateMeetingRequest,
    CreateMeetingResponse,
    MeetingResponse,
    UpdateMeetingRequest
)

from app.services.meeting_service import (
    MeetingService
)

router = APIRouter(
    tags=["Meetings"]
)


@router.post(
    "/connect",
    response_model=MeetingConnectResponse,
    status_code=status.HTTP_201_CREATED
)
def connect_provider(
    payload: MeetingConnectRequest,
    db: Session = Depends(get_db)
):
    return MeetingService.connect_provider(
        db=db,
        merchant_id=payload.merchant_id,
        provider=payload.provider,
        authorization_code=payload.authorization_code
    )

@router.post(
    "/create",
    response_model=CreateMeetingResponse,
    status_code=status.HTTP_201_CREATED
)
def create_meeting(
    payload: CreateMeetingRequest,
    db: Session = Depends(get_db)
):
    return MeetingService.create_meeting(
        db,
        payload
    )

@router.get(
    "/{id}",
    response_model=MeetingResponse,
    status_code=status.HTTP_200_OK
)
def get_meeting(
    id: UUID,
    db: Session = Depends(get_db)
):
    return MeetingService.get_meeting(
        db,
        id
    )

@router.put(
    "/{id}",
    response_model=MeetingResponse,
    status_code=status.HTTP_200_OK
)
def update_meeting(
    id: UUID,
    payload: UpdateMeetingRequest,
    db: Session = Depends(get_db)
):
    return MeetingService.update_meeting(
        db,
        id,
        payload
    )

@router.delete(
    "/{id}",
    status_code=status.HTTP_200_OK
)
def cancel_meeting(
    id: UUID,
    db: Session = Depends(get_db)
):
    return MeetingService.cancel_meeting(
        db,
        id
    )

@router.get(
    "/merchant/list",
    response_model=List[MeetingResponse],
    status_code=status.HTTP_200_OK
)
def merchant_meetings(
    merchant_id: UUID,
    db: Session = Depends(get_db)
):
    return MeetingService.get_merchant_meetings(
        db,
        merchant_id
    )