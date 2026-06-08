from sqlalchemy.orm import Session
from uuid import UUID
from app.models.meeting_model import MeetingIntegration, Meeting


class MeetingRepository:

    @staticmethod
    def create(
        db: Session,
        integration: MeetingIntegration
    ):
        db.add(integration)
        db.commit()
        db.refresh(integration)
        return integration

    @staticmethod
    def get_by_provider(
        db: Session,
        merchant_id,
        provider
    ):
        return (
            db.query(MeetingIntegration)
            .filter(
                MeetingIntegration.merchant_id == merchant_id,
                MeetingIntegration.provider == provider
            )
            .first()
        )

class MeetingRepository:

    @staticmethod
    def create_meeting(db, meeting):

        db.add(meeting)
        db.commit()
        db.refresh(meeting)

        return meeting

    @staticmethod
    def get_active_integration(
        db,
        provider
    ):
        return (
            db.query(MeetingIntegration)
            .filter(
                MeetingIntegration.provider == provider,
                MeetingIntegration.is_active == True
            )
            .first()
        )

    @staticmethod
    def get_booking_meeting(
        db,
        booking_id
    ):
        return (
            db.query(Meeting)
            .filter(Meeting.booking_id == booking_id)
            .first()
        )

    @staticmethod
    def get_by_id(db, meeting_id: UUID):

        return (
            db.query(Meeting)
            .filter(Meeting.id == meeting_id)
            .first()
        )

    @staticmethod
    def get_by_merchant(
        db,
        merchant_id: UUID
    ):

        return (
            db.query(Meeting)
            .filter(Meeting.merchant_id == merchant_id)
            .all()
        )

    @staticmethod
    def update(db, meeting):

        db.commit()
        db.refresh(meeting)

        return meeting

    @staticmethod
    def delete(db, meeting):

        db.delete(meeting)
        db.commit()