from fastapi import APIRouter, status, Depends
from app.schemas.customer_schema import ForgotPassword, ResetPassword, ChangePassword
from app.services.customer_service import *
from app.core.dependencies import get_current_user

router = APIRouter()

@router.post("/forgot-password")
def forgot_password(data: ForgotPassword):
    return forgot_password_service(data)


@router.post("/reset-password")
def reset_password(data: ResetPassword):
    return reset_password_service(data)

@router.post("/change-password")
def change_password(
    data: ChangePassword,
    current_user=Depends(get_current_user)
):
    return change_password_service(data, current_user)