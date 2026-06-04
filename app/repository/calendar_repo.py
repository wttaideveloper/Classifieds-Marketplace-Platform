from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
from app.models.calendar_model import CalendarIntegration, CalendarEvent


def create_calendar_integration(
    db: Session,
    integration: CalendarIntegration
):
    db.add(integration)
    db.commit()
    db.refresh(integration)

    return integration

def get_active_calendar_by_merchant(
    db: Session,
    merchant_id
):
    return (
        db.query(CalendarIntegration)
        .filter(
            CalendarIntegration.merchant_id == merchant_id,
            CalendarIntegration.is_active == True
        )
        .first()
    )


def get_calendar_status_repo(
    db: Session,
    merchant_id
):
    return (
        db.query(
            CalendarIntegration,
            CalendarEvent
        )
        .join(
            CalendarEvent,
            CalendarEvent.integration_id
            == CalendarIntegration.id
        )
        .filter(
            CalendarIntegration.merchant_id
            == merchant_id,
            CalendarIntegration.is_active == True
        )
        .order_by(
            CalendarEvent.created_at.desc()
        )
        .first()
    )
def get_calendar_event_by_id(
    db: Session,
    event_id: UUID
):
    return (
        db.query(CalendarEvent)
        .filter(CalendarEvent.id == event_id)
        .first()
    )


def update_calendar_event(
    db: Session,
    event: CalendarEvent
):
    db.commit()
    db.refresh(event)

    return event

def delete_calendar_event(
    db: Session,
    calendar_event: CalendarEvent
):
    db.delete(calendar_event)
    db.commit()

def get_calendar_availability(
    db: Session,
    merchant_id: UUID | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None
):
    query = db.query(CalendarEvent)

    if merchant_id:
        query = query.filter(
            CalendarEvent.merchant_id == merchant_id
        )

    if start_date:
        query = query.filter(
            CalendarEvent.start_time >= start_date
        )

    if end_date:
        query = query.filter(
            CalendarEvent.end_time <= end_date
        )

    return query.order_by(
        CalendarEvent.start_time.asc()
    ).all()