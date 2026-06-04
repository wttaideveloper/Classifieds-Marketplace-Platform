from sqlalchemy.orm import Session

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