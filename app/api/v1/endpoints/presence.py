from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.schemas.chat_schema import OnlineUsersResponse, PresenceResponse, PresenceUpdateRequest
from app.services.chat_service import (
    get_last_seen_service,
    get_online_users_service,
    update_presence_service,
)

router = APIRouter(tags=["Presence"])


@router.get(
    "/online",
    response_model=OnlineUsersResponse,
    summary="Get Online Users",
)
def get_online_users(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return get_online_users_service(db)


@router.put(
    "/status",
    response_model=PresenceResponse,
    summary="Update User Status",
)
def update_user_status(
    payload: PresenceUpdateRequest = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return update_presence_service(db, current_user, payload.status)


@router.get(
    "/{user_id}/last-seen",
    response_model=PresenceResponse,
    summary="Get Last Seen",
)
def get_last_seen(
    user_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return get_last_seen_service(db, user_id)
