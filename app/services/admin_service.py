# app/services/admin_service.py
from fastapi import status
from datetime import datetime, timedelta
import secrets
from app.services.email_service import send_email
from app.repository.admin_repo import (
    get_admin_by_email,
    get_admin_by_reset_token,
    get_admin_by_id,
    update_admin
)
from app.exceptions.custom_exception import CustomException
from app.core.security import verify_password, create_access_token, hash_password, create_refresh_token

def admin_login_service(db, payload):

    admin = get_admin_by_email(db, payload.email)

    if not admin:
        raise CustomException(status.HTTP_404_NOT_FOUND, "Admin not found")

    if not verify_password(payload.password, admin.password):
        raise CustomException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    token_data = {
        "id": admin.id,
        "email": admin.email,
        "role": "admin"
    }

    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    return {
        "success": True,
        "message": "Admin login successful",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }

    return {
        "success": True,
        "message": "Admin login successful",
        "access_token": token
    }

# FORGOT PASSWORD
def forgot_password_admin_service(db, email):

    admin = get_admin_by_email(db, email)

    if not admin:
        raise CustomException(404, "Admin not found")

    reset_token = secrets.token_urlsafe(32)

    admin.resetToken = reset_token
    admin.resetTokenExpiry = datetime.utcnow() + timedelta(minutes=15)

    update_admin(db, admin)

    send_email(admin.email, reset_token)

    return {
        "success": True,
        "message": "Reset password link sent successfully",
        "resetToken": reset_token
    }

# RESET PASSWORD
def reset_password_admin_service(
    db,
    reset_token,
    new_password,
    confirm_password
):

    if new_password != confirm_password:
        raise CustomException(400, "Passwords do not match")

    admin = get_admin_by_reset_token(db, reset_token)

    if not admin:
        raise CustomException(400, "Invalid token")

    if admin.resetTokenExpiry < datetime.utcnow():
        raise CustomException(400, "Token expired")

    admin.password = hash_password(new_password)
    admin.resetToken = None
    admin.resetTokenExpiry = None

    update_admin(db, admin)

    return {
        "success": True,
        "message": "Password reset successful"
    }


# CHANGE PASSWORD
def change_password_admin_service(
    db,
    admin_id,
    current_password,
    new_password,
    confirm_password
):

    admin = get_admin_by_id(db, admin_id)

    if not admin:
        raise CustomException(404, "Admin not found")

    if not verify_password(current_password, admin.password):
        raise CustomException(401, "Current password incorrect")

    if new_password != confirm_password:
        raise CustomException(400, "Passwords do not match")

    admin.password = hash_password(new_password)

    update_admin(db, admin)

    return {
        "success": True,
        "message": "Password changed successfully"
    }


# LOGOUT
def logout_admin_service(token, current_user):
    return {
        "success": True,
        "message": "Admin logout successful"
    }

# GET PROFILE
def get_admin_profile_service(db, admin_id):

    admin = get_admin_by_id(db, admin_id)

    if not admin:
        raise CustomException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Admin not found"
        )

    return {
        "success": True,
        "data": {
            "id": admin.id,
            "name": admin.name,
            "email": admin.email
        }
    }


# UPDATE PROFILE
def update_admin_profile_service(db, admin_id, payload):

    admin = get_admin_by_id(db, admin_id)

    if not admin:
        raise CustomException(404, "Admin not found")

    existing_email = get_admin_by_email(db, payload.email)

    if existing_email and existing_email.id != admin.id:
        raise CustomException(400, "Email already exists")

    admin.name = payload.name
    admin.email = payload.email

    update_admin(db, admin)

    return {
        "success": True,
        "message": "Profile updated successfully",
        "data": {
            "id": admin.id,
            "name": admin.name,
            "email": admin.email
        }
    }