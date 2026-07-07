from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.schemas.chat_schema import ChatEligibilityResponse, MonthlyLimitResponse
from app.services.chat_service import (
    chat_eligibility_service,
    monthly_limit_service,
    remaining_messages_service,
)

router = APIRouter(tags=["Chat Subscriptions"])


@router.get(
    "/chat-eligibility",
    response_model=ChatEligibilityResponse,
    summary="Validate Chat Eligibility",
    description="Check whether the user can send messages based on subscription plan and limits.",
)
def validate_chat_eligibility(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return chat_eligibility_service(db, current_user)


@router.get(
    "/remaining-messages",
    summary="Get Remaining Free Messages",
)
def get_remaining_messages(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return remaining_messages_service(db, current_user)


@router.get(
    "/monthly-limit",
    response_model=MonthlyLimitResponse,
    summary="Check Monthly Limit",
)
def check_monthly_limit(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return monthly_limit_service(db, current_user)
