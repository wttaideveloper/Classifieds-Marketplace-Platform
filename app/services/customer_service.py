from app.repository.customer_repo import *
from app.core.security import hash_password, verify_password, create_access_token
from app.exceptions.custom_exception import CustomException
from uuid import uuid4
from datetime import datetime, timedelta
import requests
import secrets
GOOGLE_VERIFY_URL = "https://oauth2.googleapis.com/tokeninfo"

def register_customer_service(db, customer):
    existing = get_customer_by_email(db, customer.email)

    if existing:
        raise CustomException(400, "Email already registered")

    if customer.password != customer.confirmPassword:
        raise CustomException(400, "Passwords do not match")

    if not customer.acceptTerms or not customer.acceptPrivacyPolicy:
        raise CustomException(400, "Accept terms and privacy policy")

    data = customer.dict()
    data.pop("confirmPassword")

    data["id"] = str(uuid4())
    data["password"] = hash_password(data["password"])

    new_customer = Customer(**data)
    return create_customer(db, new_customer)


def login_customer_service(db, email, password):
    user = get_customer_by_email(db, email)

    if not user:
        raise CustomException(404, "User not found")
    print("DB PASSWORD:", user.password)
    if not verify_password(password, user.password):
        raise CustomException(401, "Invalid credentials")

    token = create_access_token({"sub": user.email})

    return {"access_token": token, "token_type": "bearer"}


def google_login_service(db, google_token: str):

    # Verify token with Google
    res = requests.get(GOOGLE_VERIFY_URL, params={"id_token": google_token})

    if res.status_code != 200:
        raise CustomException(401, "Invalid Google token")

    data = res.json()

    email = data.get("email")
    name = data.get("name", "")
    google_id = data.get("sub")

    user = get_customer_by_email(db, email)

    if not user:
        user_data = Customer(
            id=str(uuid4()),
            firstName=name,
            lastName="",
            email=email,
            mobileNumber="",
            password="",
            acceptTerms=True,
            acceptPrivacyPolicy=True
        )
        user = create_customer(db, user_data)

    token = create_access_token({"sub": user.email})

    return {
        "access_token": token,
        "token_type": "bearer"
    }


def forgot_password_service(db, email: str):
    user = get_customer_by_email(db, email)

    if not user:
        raise CustomException(404, "User not found")

    reset_token = secrets.token_urlsafe(32)
    expiry = datetime.utcnow() + timedelta(minutes=15)

    user.resetToken = reset_token
    user.resetTokenExpiry = expiry

    db.commit()

    # send email here (optional)
    return {"message": "Reset link sent", "resetToken": reset_token}

def reset_password_service(db, resetToken, newPassword, confirmPassword):

    if newPassword != confirmPassword:
        raise CustomException(400, "Passwords do not match")

    user = db.query(Customer).filter(Customer.resetToken == resetToken).first()

    if not user:
        raise CustomException(404, "Invalid token")

    if user.resetTokenExpiry < datetime.utcnow():
        raise CustomException(400, "Token expired")

    user.password = hash_password(newPassword)
    user.resetToken = None
    user.resetTokenExpiry = None

    db.commit()

    return {"message": "Password reset successful"}

def change_password_service(db, user_email, currentPassword, newPassword, confirmPassword):

    if newPassword != confirmPassword:
        raise CustomException(400, "Passwords do not match")

    user = get_customer_by_email(db, user_email)

    if not user:
        raise CustomException(404, "User not found")

    if not verify_password(currentPassword, user.password):
        raise CustomException(401, "Incorrect current password")

    user.password = hash_password(newPassword)
    db.commit()

    return {"message": "Password changed successfully"}

def get_profile_service(db, cust_id):
    user = get_customer_by_id(db, cust_id)

    if not user:
        raise CustomException(404, "User not found")

    return user


def update_profile_service(db, cust_id, data):

    user = get_customer_by_id(db, cust_id)

    if not user:
        raise CustomException(404, "User not found")

    # ❌ protect password from being overwritten
    if "password" in data:
        del data["password"]

    for k, v in data.items():
        setattr(user, k, v)

    db.commit()
    db.refresh(user)

    return user