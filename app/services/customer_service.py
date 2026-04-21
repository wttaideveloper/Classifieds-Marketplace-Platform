from fastapi import status
from app.repository.customer_repo import *
from app.core.security import hash_password, verify_password, create_access_token
from app.exceptions.custom_exception import CustomException
from app.models.customer_model import customer_helper
from app.services.email_service import send_email
from google.oauth2 import id_token
from google.auth.transport.requests import Request
from app.core.config import settings
from datetime import datetime, timedelta
import secrets
import requests

GOOGLE_CLIENT_ID = settings.GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET = settings.GOOGLE_CLIENT_SECRET
GOOGLE_REDIRECT_URI = settings.GOOGLE_REDIRECT_URI

def register_customer_service(customer):
    existing = get_customer_by_email(customer.email)
    if existing:
        raise CustomException(400, "Email already registered")
    if customer.password != customer.confirmPassword:
        raise CustomException(400, "Passwords do not match")
    if not customer.acceptTerms or not customer.acceptPrivacyPolicy:
        raise CustomException(400, "Accept terms and privacy policy")
    customer_dict = customer.dict()
    customer_dict.pop("confirmPassword")
    customer_dict["password"] = hash_password(customer.password)
    customer_dict["authProvider"] = "local"
    new_customer = create_customer_repo(customer_dict)
    token = create_access_token({"cust_id": str(new_customer["_id"])})
    return {
        "message": "Customer registered successfully",
        "customer": customer_helper(new_customer), 
        "access_token": token,
        "token_type": "bearer"
    }

def login_customer_service(customer):
    db_customer = get_customer_by_email(customer.email)
    if not db_customer:
        raise CustomException(404, "Customer not found")
    # Prevent login for Google users
    if db_customer.get("authProvider") == "google":
        raise CustomException(400, "Use Google login for this account")
    if not verify_password(customer.password, db_customer["password"]):
        raise CustomException(401, "Invalid credentials")
    token = create_access_token({"user_id": str(db_customer["_id"])})
    return {
        "message": "Login successful",
        "access_token": token,
        "token_type": "bearer"
    }

def get_customer_from_auth_code(auth_code):
    token_url = "https://oauth2.googleapis.com/token"
    token_data = {
        "code": auth_code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code"
    }
    token_res = requests.post(token_url, data=token_data)
    if token_res.status_code != 200:
        raise CustomException(400, "Google token exchange failed")
    token_json = token_res.json()
    access_token = token_json.get("access_token")
    if not access_token:
        raise CustomException(400, "Failed to fetch access token")
    userinfo_res = requests.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    return userinfo_res.json()

def google_login_service(data):
    if not data.googleToken and not data.authCode:
        raise CustomException(400, "googleToken or authCode required")
    try:
        email = None
        first_name = ""
        last_name = ""
        google_id = None
        # Case 1: Google Token
        if data.googleToken:
            idinfo = id_token.verify_oauth2_token(
                data.googleToken,
                Request(),
                GOOGLE_CLIENT_ID
            )
            email = idinfo.get("email")
            first_name = idinfo.get("given_name", "")
            last_name = idinfo.get("family_name", "")
            google_id = idinfo.get("sub")
        # Case 2: authCode
        elif data.authCode:
            user_info = get_customer_from_auth_code(data.authCode)
            email = user_info.get("email")
            name = user_info.get("name", "")
            google_id = user_info.get("id")
            first_name = name.split()[0] if name else ""
            last_name = " ".join(name.split()[1:]) if name else ""
        if not email:
            raise CustomException(400, "Invalid Google data")
        db_customer = get_customer_by_email(email)
        if not db_customer:
            db_customer = create_customer_repo({
                "firstName": first_name,
                "lastName": last_name,
                "email": email,
                "mobileNumber": "",
                "googleId": google_id,
                "authProvider": "google",
                "password": None
            })
        token = create_access_token({"user_id": str(db_customer["_id"])})
        return {
            "message": "Google login successful",
            "access_token": token,
            "token_type": "bearer"
        }
    except ValueError:
        raise CustomException(401, "Invalid Google token")
    except Exception as e:
        raise CustomException(500, f"Google login failed: {str(e)}")

def forgot_password_service(data):
    customer = get_customer_by_email(data.email)
    if not customer:
        raise CustomException(404, "Customer not found")
    # 1. Generate token
    reset_token = secrets.token_urlsafe(32)
    expiry = datetime.utcnow() + timedelta(minutes=15)
    # 2. Save token in DB
    update_customer_repo(str(customer["_id"]), {
        "resetToken": reset_token,
        "resetTokenExpiry": expiry
    })
    # 3. Create reset link
    reset_link = f"http://localhost:3000/reset-password?token={reset_token}"
    # 4. Send email
    send_email(customer["email"], reset_link)
    return {
        "message": "Password reset link sent"
    }

def reset_password_service(data):
    if data.newPassword != data.confirmPassword:
        raise CustomException(400, "Passwords do not match")
    customer = get_customer_by_reset_token(data.resetToken)
    if not customer:
        raise CustomException(400, "Invalid reset token")
    expiry = customer.get("resetTokenExpiry")
    if not expiry or expiry < datetime.utcnow():
        raise CustomException(400, "Reset token expired")
    update_customer_repo(str(customer["_id"]), {
        "password": hash_password(data.newPassword),
        "resetToken": None,
        "resetTokenExpiry": None
    })
    return {
        "message": "Password reset successful"
    }

def change_password_service(data, current_user):
    # Validate passwords
    if data.newPassword != data.confirmPassword:
        raise CustomException(400, "Passwords do not match")

    # Validate token payload
    if not current_user or "id" not in current_user:
        raise CustomException(401, "Invalid token payload")

    # Get user
    db_customer = get_customer_by_id(current_user["id"])
    if not db_customer:
        raise CustomException(404, "Customer not found")

    # Google user restriction
    if db_customer.get("authProvider") == "google":
        raise CustomException(400, "Google users cannot change password")
    print("DB HASH:", db_customer["password"])
    print("INPUT:", data.currentPassword)
    # Verify current password
    if not verify_password(data.currentPassword, db_customer["password"]):
        raise CustomException(401, "Current password is incorrect")
    # Update password
    update_customer_repo(str(db_customer["_id"]), {
        "password": hash_password(data.newPassword)
    })

    return {
        "message": "Password changed successfully"
    }

def get_customer_profile_service(current_user):
    customer = get_customer_by_email(current_user["email"])
    if not customer:
        raise CustomException(404, "Customer not found")
    return customer

def update_customer_profile_service(current_user, payload):
    customer = get_customer_by_email(current_user["email"])
    if not customer:
        raise CustomException(404, "Customer not found")
    update_data = {k: v for k, v in payload.dict().items() if v is not None}
    if not update_data:
        raise CustomException(400, "No data provided to update")
    update_customer_profile(current_user["email"], update_data)
    return {"message": "Profile updated successfully"}
