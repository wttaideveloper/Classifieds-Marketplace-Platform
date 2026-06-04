import uuid

from datetime import datetime, timedelta

from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.models.calendar_model import (
    CalendarIntegration
)

from app.repository.calendar_repo import (
    create_calendar_integration,
    get_active_calendar_by_merchant,
    get_calendar_status_repo
)

from app.schemas.calendar_schema import (
    CalendarConnectRequest,
    CalendarEventCreateRequest
)


def connect_calendar_service(
    db: Session,
    payload: CalendarConnectRequest
):
    """
    Simulated OAuth token exchange.
    Replace with Google/Outlook API call.
    """

    integration = CalendarIntegration(
        merchant_id=payload.merchant_id,
        provider=payload.provider,
        provider_account_id=str(uuid.uuid4()),
        access_token=f"access_{uuid.uuid4()}",
        refresh_token=f"refresh_{uuid.uuid4()}",
        token_expiry=datetime.utcnow() + timedelta(days=30),
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

    """
    Simulated provider event creation.
    Replace with Google Calendar API
    or Microsoft Graph API.
    """

    event_id = str(uuid.uuid4())

    return {
        "integration_id": integration.id,
        "provider": integration.provider,
        "event_id": event_id,
        "meeting_link": f"https://meeting.example.com/{event_id}",
        "event_status": "CONFIRMED"
 
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