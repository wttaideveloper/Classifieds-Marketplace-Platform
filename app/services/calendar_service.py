import uuid
from uuid import UUID
from datetime import datetime, timedelta

from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.models.calendar_model import (
    CalendarIntegration,
    CalendarEvent,
    EventStatusEnum
)

from app.repository.calendar_repo import (
    create_calendar_integration,
    get_active_calendar_by_merchant,
    get_calendar_status_repo,
    get_calendar_event_by_id,
    update_calendar_event,
    delete_calendar_event,
    get_calendar_availability,
    create_calendar_event
)

from app.schemas.calendar_schema import (
    CalendarConnectRequest,
    CalendarEventCreateRequest,
    CalendarEventUpdateRequest
)

def connect_calendar_service(
    db: Session,
    payload: CalendarConnectRequest
):
    existing = get_active_calendar_by_merchant(
        db,
        payload.merchant_id
    )

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Calendar already connected"
        )

    integration = CalendarIntegration(
        merchant_id=payload.merchant_id,
        provider=payload.provider,
        provider_account_id=str(uuid.uuid4()),
        access_token="N/A",
        refresh_token="N/A",
        token_expiry=datetime.utcnow() + timedelta(days=365),
        is_active=True
    )

    integration = create_calendar_integration(
        db,
        integration
    )

    return {
        "integration_id": integration.id,
        "provider": integration.provider,
        "message": "Calendar connected successfully"
    }
def create_calendar_event_service(
    db: Session,
    merchant_id,
    payload: CalendarEventCreateRequest
):
    integration = get_active_calendar_by_merchant(
        db,
        merchant_id
    )

    if not integration:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Calendar integration not found"
        )

    if payload.end_time <= payload.start_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="End time must be greater than start time"
        )

    external_event_id = str(uuid.uuid4())

    calendar_event = CalendarEvent(
        booking_id=payload.booking_id,
        merchant_id=merchant_id,
        provider=integration.provider,
        external_event_id=external_event_id,
        event_title=payload.event_title,
        start_time=payload.start_time,
        end_time=payload.end_time,
        meeting_link=f"https://meeting.example.com/{external_event_id}",
        event_status=EventStatusEnum.SCHEDULED
    )

    calendar_event = create_calendar_event(
        db,
        calendar_event
    )

    return {
        "integration_id": integration.id,
        "provider": integration.provider,
        "event_id": str(calendar_event.id),
        "meeting_link": calendar_event.meeting_link,
        "event_status": calendar_event.event_status
    }

def get_calendar_status_service(
    db: Session,
    merchant_id
):
    result = get_calendar_status_repo(
        db,
        merchant_id
    )

    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Calendar integration not found"
        )

    integration, event = result

    return {
        "integration_id": integration.id,
        "provider": integration.provider,
        "is_active": integration.is_active,
        "event_status": event.status,
        "last_synced_at": integration.updated_at
    }

def update_calendar_event_service(
    db: Session,
    event_id: UUID,
    payload: CalendarEventUpdateRequest
):
    event = get_calendar_event_by_id(
        db,
        event_id
    )

    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Calendar event not found"
        )

    start_time = payload.start_time or event.start_time
    end_time = payload.end_time or event.end_time

    if end_time <= start_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="End time must be greater than start time"
        )

    if payload.event_title is not None:
        event.event_title = payload.event_title

    if payload.start_time is not None:
        event.start_time = payload.start_time

    if payload.end_time is not None:
        event.end_time = payload.end_time

    event.event_status = EventStatusEnum.UPDATED

    updated_event = update_calendar_event(
        db,
        event
    )

    return {
        "event_id": updated_event.id,
        "booking_id": updated_event.booking_id,
        "event_title": updated_event.event_title,
        "start_time": updated_event.start_time,
        "end_time": updated_event.end_time,
        "meeting_link": updated_event.meeting_link,
        "event_status": updated_event.event_status
    }

def delete_calendar_event_service(
    db: Session,
    event_id: UUID
):
    calendar_event = get_calendar_event_by_id(
        db,
        event_id
    )

    if not calendar_event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Calendar event not found"
        )

    delete_calendar_event(
        db,
        calendar_event
    )

    return {
        "message": "Calendar event deleted successfully",
        "event_id": event_id
    }

def get_calendar_availability_service(
    db: Session,
    merchant_id: UUID | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None
):
    try:
        events = get_calendar_availability(
            db=db,
            merchant_id=merchant_id,
            start_date=start_date,
            end_date=end_date
        )

        return {
            "total_records": len(events),
            "events": events
        }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch calendar availability: {str(e)}"
        )