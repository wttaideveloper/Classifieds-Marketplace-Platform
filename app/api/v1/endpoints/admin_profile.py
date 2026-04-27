# app/api/v1/endpoints/admin_profile.py
from fastapi import APIRouter, Depends, status, HTTPException
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.core.dependencies import get_current_user
from app.schemas.admin_schema import AdminProfileUpdate
from app.services.admin_service import (
    get_admin_profile_service,
    update_admin_profile_service
)

router = APIRouter(tags=["Admin Profile"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# GET ADMIN PROFILE
@router.get("/profile", status_code=status.HTTP_200_OK)
def get_profile(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):

    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Unauthorized"
        )

    return get_admin_profile_service(
        db,
        current_user.get("id")
    )

# UPDATE ADMIN PROFILE
@router.put("/profile", status_code=status.HTTP_200_OK)
def update_profile(
    payload: AdminProfileUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):

    if current_user.get("role") != "admin":
        raise Exception("Unauthorized")

    return update_admin_profile_service(
        db,
        current_user.get("id"),
        payload
    )