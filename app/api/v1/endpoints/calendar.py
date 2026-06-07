from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    status,
    Query
)
from sqlalchemy.orm import Session
from datetime import datetime
from app.db.database import get_db
from app.schemas.calendar_schema import (
    CalendarConnectRequest,
    CalendarConnectResponse,
    CalendarEventCreateRequest,
    CalendarEventCreateResponse,
    CalendarStatusResponse,
    CalendarEventUpdateRequest,
    CalendarEventUpdateResponse,
    CalendarDeleteResponse,
    CalendarAvailabilityResponse
)

from app.services.calendar_service import (
    connect_calendar_service,
    create_calendar_event_service,
    get_calendar_status_service,
    update_calendar_event_service,
    delete_calendar_event_service,
    get_calendar_availability_service
)

router = APIRouter(
    tags=["Calendar"]
)


@router.post(
    "/connect",
    response_model=CalendarConnectResponse,
    status_code=status.HTTP_201_CREATED
)
def connect_calendar(
    payload: CalendarConnectRequest,
    db: Session = Depends(get_db)
):
    return connect_calendar_service(
        db,
        payload
    )


@router.post(
    "/events",
    response_model=CalendarEventCreateResponse,
    status_code=status.HTTP_201_CREATED
)
def create_event(
    merchant_id: UUID,
    payload: CalendarEventCreateRequest,
    db: Session = Depends(get_db)
):
    return create_calendar_event_service(
        db,
        merchant_id,
        payload
    )

@router.get(
    "/status",
    response_model=CalendarStatusResponse,
    status_code=status.HTTP_200_OK
)
def get_calendar_status(
    merchant_id: UUID = Query(...),
    db: Session = Depends(get_db)
):
    return get_calendar_status_service(
        db,
        merchant_id
    )

@router.put(
    "/events/{id}",
    response_model=CalendarEventUpdateResponse,
    status_code=status.HTTP_200_OK
)
def update_calendar_event(
    id: UUID,
    payload: CalendarEventUpdateRequest,
    db: Session = Depends(get_db)
):
    return update_calendar_event_service(
        db,
        id,
        payload
    )

@router.delete(
    "/events/{id}",
    response_model=CalendarDeleteResponse,
    status_code=status.HTTP_200_OK
)
def delete_calendar_event(
    id: UUID,
    db: Session = Depends(get_db)
):
    return delete_calendar_event_service(
        db=db,
        event_id=id
    )

@router.get(
    "/availability",
    response_model=CalendarAvailabilityResponse,
    status_code=status.HTTP_200_OK
)
def get_availability(
    merchant_id: UUID | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    db: Session = Depends(get_db)
):
    return get_calendar_availability_service(
        db=db,
        merchant_id=merchant_id,
        start_date=start_date,
        end_date=end_date
    )