from app.repository.merchant_repo import get_merchant_by_email, create_merchant, update_merchant, get_merchant_by_id
from app.models.merchant_model import Merchant
from app.exceptions.custom_exception import CustomException
from datetime import datetime, timedelta
from app.core.security import hash_password, verify_password, create_access_token
from app.services.email_service import send_email 
from app.core.token_blacklist import TOKEN_BLACKLIST
from uuid import uuid4
import requests
import secrets

GOOGLE_VERIFY_URL = "https://oauth2.googleapis.com/tokeninfo"

def register_merchant_service(db, merchant):
    print(db.bind.url)

    #  Check existing email
    existing = get_merchant_by_email(db, merchant.businessEmail)
    if existing:
        raise CustomException(400, "Email already registered")

    #  Password match check
    if merchant.password != merchant.confirmPassword:
        raise CustomException(400, "Passwords do not match")

    #  Terms check
    if not merchant.acceptTerms or not merchant.acceptPrivacyPolicy:
        raise CustomException(400, "Accept terms and privacy policy")

    #  Prepare data
    data = merchant.dict()
    data.pop("confirmPassword")

    data["id"] = str(uuid4())
    data["password"] = hash_password(data["password"])

    #  Create merchant object
    new_merchant = Merchant(**data)

    # Save to DB
    return create_merchant(db, new_merchant)
    

# LOGIN
def login_merchant_service(db, email: str, password: str):

    #  Check if merchant exists
    merchant = get_merchant_by_email(db, email)
    if not merchant:
        raise CustomException(404, "Merchant not found")

    #  Verify password
    if not verify_password(password, merchant.password):
        raise CustomException(401, "Invalid credentials")

    #  Generate JWT token
    token = create_access_token({
        "sub": merchant.id,
        "email": merchant.businessEmail,
        "role": "merchant"
    })

    return {
        "access_token": token,
        "token_type": "bearer"
    }

# GOOGLE LOGIN
def google_login_service(db, google_token: str):
    res = requests.get(GOOGLE_VERIFY_URL, params={"id_token": google_token})

    if res.status_code != 200:
        raise CustomException(401, "Invalid Google token")

    data = res.json()
    email = data.get("email")
    name = data.get("name", "")

    merchant = get_merchant_by_email(db, email)

    if not merchant:
        merchant_data = Merchant(
            id=str(uuid4()),
            fullName=name,
            businessEmail=email,
            mobileNumber="",
            password=hash_password(secrets.token_urlsafe(16)),
            acceptTerms=True,
            acceptPrivacyPolicy=True
        )
        merchant = create_merchant(db, merchant_data)

    token = create_access_token({
        "sub": merchant.id,
        "email": merchant.businessEmail,
        "role": "merchant"
    })

    return {
        "access_token": token,
        "token_type": "bearer"
    }

# FORGOT PASSWORD 
def forgot_password_merchant_service(db, email: str):

    merchants = db.query(Merchant).all()
    print("ALL MERCHANTS:", merchants)
    # Check merchant exists
    merchant = get_merchant_by_email(db, email)
    print("FOUND MERCHANT:", merchant)
    if not merchant:
        raise CustomException(404, "Merchant not found")

    # Generate token
    reset_token = secrets.token_urlsafe(32)
    expiry = datetime.utcnow() + timedelta(minutes=15)

    # Save in DB
    merchant.resetToken = reset_token
    merchant.resetTokenExpiry = expiry
    update_merchant(db, merchant)

    # Create reset link
    reset_link = f"http://localhost:8000/reset-password?token={reset_token}"

    # Send email
    email_sent = send_email(merchant.businessEmail, reset_link)

    if not email_sent:
        raise CustomException(500, "Failed to send reset email")

    return {
        "message": "Password reset link sent successfully"
    }

# RESET PASSWORD
def reset_password_merchant_service(db, resetToken, newPassword, confirmPassword):
    if newPassword != confirmPassword:
        raise CustomException(400, "Passwords do not match")

    user = db.query(Merchant).filter(Merchant.resetToken == resetToken).first()

    if not user:
       raise CustomException(400, "Invalid or expired token")

    if not user.resetTokenExpiry or user.resetTokenExpiry < datetime.utcnow():
        raise CustomException(400, "Token expired")

    user.password = hash_password(newPassword)
    user.resetToken = None
    user.resetTokenExpiry = None
    db.commit()

    return {"message": "Password reset successful"}

# CHANGE PASSWORD
def change_password_merchant_service(db, merchant_id, currentPassword, newPassword, confirmPassword):

    # Validate new passwords match
    if newPassword != confirmPassword:
        raise CustomException(400, "Passwords do not match")

    # Fetch merchant
    merchant = get_merchant_by_id(db, merchant_id)
    if not merchant:
        raise CustomException(404, "Merchant not found")

    # Verify current password
    if not verify_password(currentPassword, merchant.password):
        raise CustomException(401, "Incorrect current password")

    # Prevent same password reuse (optional but good)
    if verify_password(newPassword, merchant.password):
        raise CustomException(400, "New password cannot be same as current password")

    # Update password
    merchant.password = hash_password(newPassword)

    update_merchant(db, merchant)

    return {
        "message": "Password changed successfully"
    }

# LOGOUT 
def logout_merchant_service(token: str, current_user):

    # Add token to blacklist
    TOKEN_BLACKLIST.add(token)

    return {
        "message": "Logged out successfully"
    }

# PROFILE
def get_merchant_profile_service(db, merchant_id: str):

    merchant = get_merchant_by_id(db, merchant_id)

    if not merchant:
        raise CustomException(404, "Merchant not found")

    return {
        "id": merchant.id,
        "fullName": merchant.fullName,
        "businessEmail": merchant.businessEmail,
        "mobileNumber": merchant.mobileNumber,
        "businessName": merchant.businessName
    }

def update_merchant_profile_service(db, merchant_id: str, data):

    merchant = get_merchant_by_id(db, merchant_id)

    if not merchant:
        raise CustomException(404, "Merchant not found")

    # Allowed fields only
    allowed_fields = {
        "name",
        "mobileNumber",
        "profileImage"
    }

    # Update only provided fields
    for field, value in data.items():
        if field in allowed_fields and value is not None:
            if field == "name":
                merchant.fullName = value
            else:
                setattr(merchant, field, value)

    update_merchant(db, merchant)

    return {
        "message": "Profile updated successfully"
    }