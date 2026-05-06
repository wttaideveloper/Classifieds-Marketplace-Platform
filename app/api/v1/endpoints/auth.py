from fastapi import APIRouter, status, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.schemas.customer_schema import (
    ForgotPassword,
    ResetPassword,
    ChangePassword
)
from app.schemas.common_schema import RefreshTokenSchema, ResendVerificationSchema
from app.exceptions.custom_exception import CustomException
from app.schemas.common_schema import VerifyEmailSchema
from app.services.customer_service import (
    forgot_password_service,
    reset_password_service,
    change_password_service,
    logout_customer_service
)
from app.services.merchant_service import (
    forgot_password_merchant_service,
    reset_password_merchant_service,
    change_password_merchant_service,
    logout_merchant_service
)
from app.services.admin_service import (
    forgot_password_admin_service,
    reset_password_admin_service,
    change_password_admin_service,
    logout_admin_service
)
from app.services.common_service import (
    verify_email_service,
    refresh_token_service,
    get_current_user_service,
    resend_verification_service
)
from app.models.customer_model import Customer
from app.models.merchant_model import Merchant
from app.models.admin_model import Admin

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

    if payload.role == "customer":
        return forgot_password_service(db, payload.email)

    elif payload.role == "merchant":
        return forgot_password_merchant_service(db, payload.email)

    elif payload.role == "admin":
        return forgot_password_admin_service(db, payload.email)

    else:
        raise CustomException(400, "Invalid role")

# RESET PASSWORD
@router.post("/reset-password", status_code=status.HTTP_200_OK)
def reset_password(payload: ResetPassword, db: Session = Depends(get_db)):

    # customer
    user = db.query(Customer).filter(
        Customer.resetToken == payload.resetToken
    ).first()

    if user:
        return reset_password_service(
            db,
            payload.resetToken,
            payload.newPassword,
            payload.confirmPassword
        )

    # merchant
    user = db.query(Merchant).filter(
        Merchant.resetToken == payload.resetToken
    ).first()

    if user:
        return reset_password_merchant_service(
            db,
            payload.resetToken,
            payload.newPassword,
            payload.confirmPassword
        )

    # admin
    user = db.query(Admin).filter(
        Admin.resetToken == payload.resetToken
    ).first()

    if user:
        return reset_password_admin_service(
            db,
            payload.resetToken,
            payload.newPassword,
            payload.confirmPassword
        )

    raise CustomException(400, "Invalid or expired token")

# CHANGE PASSWORD
@router.post("/change-password", status_code=status.HTTP_200_OK)
def change_password(
    payload: ChangePassword,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):

    if payload.newPassword != payload.confirmPassword:
        raise HTTPException(400, "Passwords do not match")

    role = current_user.get("role")
    user_id = current_user.get("id")

    if role == "customer":
        return change_password_service(
            db,
            user_id,
            payload.currentPassword,
            payload.newPassword,
            payload.confirmPassword
        )

    elif role == "merchant":
        return change_password_merchant_service(
            db,
            user_id,
            payload.currentPassword,
            payload.newPassword,
            payload.confirmPassword
        )

    elif role == "admin":
        return change_password_admin_service(
            db,
            user_id,
            payload.currentPassword,
            payload.newPassword,
            payload.confirmPassword
        )

    else:
        raise CustomException(403, "Invalid role")

# LOGOUT
@router.post("/logout", status_code=status.HTTP_200_OK)
def logout(
    request: Request,
    current_user=Depends(get_current_user)
):

    auth_header = request.headers.get("Authorization")

    if not auth_header:
        raise HTTPException(401, "Authorization header missing")

    token = auth_header.replace("Bearer ", "")
    role = current_user.get("role")

    if role == "customer":
        return logout_customer_service(token, current_user)

    elif role == "merchant":
        return logout_merchant_service(token, current_user)

    elif role == "admin":
        return logout_admin_service(token, current_user)

    else:
        raise HTTPException(403, "Invalid role")

# REFRESH TOKEN
@router.post("/refresh-token", status_code=status.HTTP_200_OK)
def refresh_token(payload: RefreshTokenSchema):
    return refresh_token_service(payload)

# Current Logged-in user
@router.get("/me", status_code=status.HTTP_200_OK)
def get_logged_in_user(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    return get_current_user_service(db, current_user)

# Verify email
@router.post("/verify-email", status_code=status.HTTP_200_OK)
def verify_email(
    payload: VerifyEmailSchema,
    db: Session = Depends(get_db)
):
    return verify_email_service(db, payload)

# Resend verification email
@router.post(
    "/resend-verification",
    status_code=status.HTTP_200_OK
)
def resend_verification(
    payload: ResendVerificationSchema,
    db: Session = Depends(get_db)
):
    return resend_verification_service(db, payload)