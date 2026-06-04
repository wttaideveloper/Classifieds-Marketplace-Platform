from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    status,
    Query
)

from sqlalchemy.orm import Session

from app.db.database import get_db

from app.schemas.calendar_schema import (
    CalendarConnectRequest,
    CalendarConnectResponse,
    CalendarEventCreateRequest,
    CalendarEventCreateResponse,
    CalendarStatusResponse
)

from app.services.calendar_service import (
    connect_calendar_service,
    create_calendar_event_service,
    get_calendar_status_service
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