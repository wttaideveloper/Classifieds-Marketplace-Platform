from fastapi import APIRouter, status, Depends, HTTPException
from sqlalchemy.orm import Session

from app.schemas.customer_schema import (
    ForgotPassword,
    ResetPassword,
    ChangePassword
)

from app.services.customer_service import (
    forgot_password_service,
    reset_password_service,
    change_password_service
)

from app.core.dependencies import get_current_user
from app.db.database import SessionLocal


router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# FORGOT PASSWORD
@router.post("/forgot-password", status_code=status.HTTP_200_OK)
def forgot_password(payload: ForgotPassword, db: Session = Depends(get_db)):
    return forgot_password_service(db, payload.email)


#  RESET PASSWORD
@router.post("/reset-password", status_code=status.HTTP_200_OK)
def reset_password(payload: ResetPassword, db: Session = Depends(get_db)):
    return reset_password_service(
        db,
        payload.resetToken,
        payload.newPassword,
        payload.confirmPassword
    )


#  CHANGE PASSWORD (JWT PROTECTED)
@router.post("/change-password", status_code=status.HTTP_200_OK)
def change_password(
    payload: ChangePassword,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    # validate passwords early
    if payload.newPassword != payload.confirmPassword:
        raise HTTPException(
            status_code=400,
            detail="Passwords do not match"
        )

    return change_password_service(
        db,
        current_user["id"],   
        payload.currentPassword,
        payload.newPassword,
        payload.confirmPassword
    )