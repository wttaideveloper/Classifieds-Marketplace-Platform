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
    connect_provider_service,
    create_meeting_service,
    get_meeting_service,
    update_meeting_service,
    cancel_meeting_service,
    get_merchant_meetings_service
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
    return connect_provider_service(
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
    return create_meeting_service(
        db,
        payload
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
    return get_merchant_meetings_service(
        db,
        merchant_id
    )

@router.get(
    "/{meeting_id}",
    response_model=MeetingResponse,
    status_code=status.HTTP_200_OK
)
def get_meeting(
    meeting_id: UUID,
    db: Session = Depends(get_db)
):
    return get_meeting_service(
        db,
        meeting_id
    )

@router.put(
    "/{meeting_id}",
    response_model=MeetingResponse,
    status_code=status.HTTP_200_OK
)
def update_meeting(
    meeting_id: UUID,
    payload: UpdateMeetingRequest,
    db: Session = Depends(get_db)
):
    return update_meeting_service(
        db,
        meeting_id,
        payload
    )

@router.delete(
    "/{meeting_id}",
    status_code=status.HTTP_200_OK
)
def cancel_meeting(
    meeting_id: UUID,
    db: Session = Depends(get_db)
):
    return cancel_meeting_service(
        db,
        meeting_id
    )

