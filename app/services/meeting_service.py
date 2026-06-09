from datetime import datetime, timedelta
from uuid import uuid4
from uuid import UUID
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from app.models.meeting_model import MeetingIntegration, MeetingStatusEnum, MeetingProviderEnum, Meeting
from app.repository.meeting_repo import get_by_provider, get_booking_meeting, create, get_active_integration, get_by_id, get_by_merchant, create_meeting, update, delete


def connect_provider_service(
        db: Session,
        merchant_id,
        provider,
        authorization_code
    ):

        existing = get_by_provider(
            db,
            merchant_id,
            provider
        )

        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Provider already connected"
            )

        """
        Production Flow:

        Zoom:
            POST https://zoom.us/oauth/token

        Google Meet:
            POST https://oauth2.googleapis.com/token

        Teams:
            POST https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token
        """

        access_token = f"access_{authorization_code}"
        refresh_token = f"refresh_{authorization_code}"

        integration = MeetingIntegration(
            merchant_id=merchant_id,
            provider=provider,
            provider_account_id="provider-user-001",
            access_token=access_token,
            refresh_token=refresh_token,
            token_expiry=datetime.utcnow() + timedelta(hours=1),
            is_active=True
        )

        return create(
            db,
            integration
        )


def create_meeting_service(
        db,
        payload
    ):

        if payload.start_time >= payload.end_time:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="End time must be greater than start time"
            )

        existing = get_booking_meeting(
            db,
            payload.booking_id
        )

        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Meeting already exists for booking"
            )

        integration = (
            get_active_integration(
                db,
                payload.provider
            )
        )

        if not integration:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{payload.provider} integration not connected"
            )

        provider_meeting_id = str(uuid4())

        if payload.provider == MeetingProviderEnum.ZOOM:

            meeting_link = (
                f"https://zoom.us/j/{provider_meeting_id}"
            )

            password = "zoom123"

        elif payload.provider == MeetingProviderEnum.GOOGLE_MEET:

            meeting_link = (
                f"https://meet.google.com/{provider_meeting_id[:10]}"
            )

            password = None

        elif payload.provider == MeetingProviderEnum.TEAMS:

            meeting_link = (
                f"https://teams.microsoft.com/l/meetup-join/{provider_meeting_id}"
            )

            password = None

        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid provider"
            )

        meeting = Meeting(
            booking_id=payload.booking_id,
            merchant_id=integration.merchant_id,
            provider=payload.provider,
            external_meeting_id=provider_meeting_id,
            meeting_title=payload.meeting_title,
            meeting_link=meeting_link,
            meeting_password=password,
            start_time=payload.start_time,
            end_time=payload.end_time,
            meeting_status=MeetingStatusEnum.SCHEDULED
        )

        saved_meeting = (
            create_meeting(
                db,
                meeting
            )
        )

        return {
            "meeting_id": saved_meeting.id,
            "meeting_link": saved_meeting.meeting_link,
            "provider": saved_meeting.provider,
            "meeting_status": saved_meeting.meeting_status,
            "start_time": saved_meeting.start_time
        }


def get_meeting_service(
        db,
        meeting_id: UUID
    ):

        meeting = get_by_id(
            db,
            meeting_id
        )

        if not meeting:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Meeting not found"
            )

        return meeting

def get_merchant_meetings_service(
        db,
        merchant_id: UUID
    ):

        return get_by_merchant(
            db,
            merchant_id
        )

   
def update_meeting_service(
        db,
        meeting_id,
        payload
    ):

        meeting = get_by_id(
            db,
            meeting_id
        )

        if not meeting:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Meeting not found"
            )

        if (
            payload.start_time
            and payload.end_time
            and payload.start_time >= payload.end_time
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="End time must be greater than start time"
            )

        if payload.meeting_title:
            meeting.meeting_title = payload.meeting_title

        if payload.start_time:
            meeting.start_time = payload.start_time

        if payload.end_time:
            meeting.end_time = payload.end_time

        """
        Zoom Update
        PATCH /meetings/{meetingId}

        Google Meet
        Update Google Calendar Event

        Teams
        PATCH /onlineMeetings/{meetingId}
        """

        return update(
            db,
            meeting
        )

   
def cancel_meeting_service(
        db,
        meeting_id
    ):

        meeting = get_by_id(
            db,
            meeting_id
        )

        if not meeting:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Meeting not found"
            )

        """
        Zoom:
        DELETE /meetings/{meetingId}

        Google:
        DELETE Event

        Teams:
        DELETE Online Meeting
        """

        meeting.meeting_status = (
            MeetingStatusEnum.CANCELLED
        )

        update(
            db,
            meeting
        )

        return {
            "message": "Meeting cancelled successfully"
        }