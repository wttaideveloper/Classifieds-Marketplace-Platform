from jose import jwt, JWTError, ExpiredSignatureError
from fastapi import status, HTTPException
from app.core.config import settings
from app.exceptions.custom_exception import CustomException
from app.core.security import hash_password, verify_password, create_access_token, create_refresh_token
from app.models.customer_model import Customer
from app.models.merchant_model import Merchant
from app.models.admin_model import Admin
from app.services.email_service import send_email
from app.repository.customer_repo import get_customer_by_id
from app.repository.merchant_repo import get_merchant_by_id
from app.repository.admin_repo import get_admin_by_id, get_admin_by_email
from app.core.token_blacklist import TOKEN_BLACKLIST
from datetime import datetime, timedelta
import secrets


def refresh_token_service(payload):
    token = payload.refreshToken
    try:
        decoded = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if decoded.get("type") != "refresh":
            raise CustomException(401, "Invalid refresh token")
        user_data = {
            "id": decoded.get("id"),
            "email": decoded.get("email"),
            "role": decoded.get("role")
        }
        return {
            "success": True,
            "message": "Access token refreshed successfully",
            "accessToken": create_access_token(user_data)
        }
    except ExpiredSignatureError:
        raise CustomException(401, "Refresh token expired")
    except JWTError:
        raise CustomException(401, "Invalid refresh token")


def verify_email_service(db, payload):
    token = payload.verificationToken
    if not token:
        raise CustomException(400, "Verification token is required")
    user = (
        db.query(Customer).filter(Customer.verificationToken == token).first()
        or db.query(Merchant).filter(Merchant.verificationToken == token).first()
        or db.query(Admin).filter(Admin.verificationToken == token).first()
    )
    if not user:
        raise CustomException(404, "Invalid verification token")
    if user.isEmailVerified:
        raise CustomException(400, "Email already verified")
    user.isEmailVerified = True
    user.verificationToken = None
    db.commit()
    return {"success": True, "message": "Email verified successfully"}


def resend_verification_service(db, payload):
    email = payload.email
    user = (
        db.query(Customer).filter(Customer.email == email).first()
        or db.query(Merchant).filter(Merchant.businessEmail == email).first()
        or db.query(Admin).filter(Admin.email == email).first()
    )
    if not user:
        raise CustomException(404, "User not found")
    if user.isEmailVerified:
        raise CustomException(400, "Email already verified")
    verification_token = secrets.token_urlsafe(32)
    user.verificationToken = verification_token
    db.commit()
    send_email(email, verification_token)
    return {"success": True, "message": "Verification email sent successfully"}


def get_current_user_service(db, current_user):

    role = current_user.get("role")
    user_id = current_user.get("id")

    if role == "customer":
        user = get_customer_by_id(db, user_id)

        if not user:
            raise CustomException(404, "Customer not found")

        return {
            "success": True,
            "data": {
                "id": user.id,
                "email": user.email,
                "role": "customer"
            }
        }

    elif role == "merchant":
        user = get_merchant_by_id(db, user_id)

        if not user:
            raise CustomException(404, "Merchant not found")

        return {
            "success": True,
            "data": {
                "id": user.id,
                "email": user.businessEmail,   # FIXED
                "role": "merchant"
            }
        }
    elif role == "admin":
        user = get_admin_by_id(db, user_id)

        if not user:
            raise CustomException(404, "Admin not found")

        return {
            "success": True,
            "data": {
                "id": user.id,
                "email": user.email,
                "role": "admin"
            }
        }

    else:
        raise CustomException(403, "Invalid role")


def validate_role(role: str):
    allowed_roles = ["customer", "merchant", "admin"]
    if role.lower() not in allowed_roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid role")
    return True