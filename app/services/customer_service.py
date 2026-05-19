from fastapi import status
from sqlalchemy.orm import Session
from app.repository.customer_repo import *
from app.core.security import hash_password, verify_password, create_access_token, create_refresh_token
from app.exceptions.custom_exception import CustomException
from app.models.customer_model import Customer
from app.services.email_service import send_email
from app.core.token_blacklist import TOKEN_BLACKLIST
from uuid import uuid4
from datetime import datetime, timedelta
import requests
import secrets

GOOGLE_VERIFY_URL = "https://oauth2.googleapis.com/tokeninfo"

# GOOGLE_VERIFY_URL = "https://oauth2.googleapis.com/tokeninfo"

# REGISTER
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
    create_customer(db, new_customer)
    return {"success": True, "message": "Registration successful"}

# LOGIN
def login_customer_service(db, email, password):
    user = get_customer_by_email(db, email)
    if not user:
        raise CustomException(404, "User not found")
    if not verify_password(password, user.password):
        raise CustomException(401, "Invalid credentials")
    token_data = {"id": str(user.id), "email": user.email, "role": "customer"}
    return {
        "success": True,
        "message": "Login successful",
        "accessToken": create_access_token(token_data),
        "refreshToken": create_refresh_token(token_data),
        "tokenType": "bearer"
    }

# GOOGLE LOGIN
def google_login_service(db, google_token: str):
    res = requests.get(GOOGLE_VERIFY_URL, params={"id_token": google_token})
    if res.status_code != 200:
        raise CustomException(401, "Invalid Google token")
    data = res.json()
    email = data.get("email")
    name = data.get("name", "")
    user = get_customer_by_email(db, email)
    if not user:
        parts = name.split(" ", 1)
        user = Customer(
            id=str(uuid4()),
            firstName=parts[0],
            lastName=parts[1] if len(parts) > 1 else "",
            email=email,
            mobileNumber="",
            password=hash_password(secrets.token_urlsafe(16)),
            acceptTerms=True,
            acceptPrivacyPolicy=True
        )
        create_customer(db, user)
    token_data = {"id": str(user.id), "email": user.email, "role": "customer"}
    return {
        "success": True,
        "accessToken": create_access_token(token_data),
        "refreshToken": create_refresh_token(token_data),
        "tokenType": "bearer"
    }

# FORGOT PASSWORD
def forgot_password_service(db, email: str):
    user = get_customer_by_email(db, email)
    if not user:
        raise CustomException(404, "User not found")
    reset_token = secrets.token_urlsafe(32)
    user.resetToken = reset_token
    user.resetTokenExpiry = datetime.utcnow() + timedelta(minutes=15)
    db.commit()
    reset_link = f"http://localhost:8000/reset-password?token={reset_token}"
    if not send_email(user.email, reset_link):
        raise CustomException(500, "Failed to send email")
    return {"success": True, "message": "Reset link sent successfully"}

# RESET PASSWORD
def reset_password_service(db, resetToken, newPassword, confirmPassword):
    if newPassword != confirmPassword:
        raise CustomException(400, "Passwords do not match")
    user = db.query(Customer).filter(Customer.resetToken == resetToken).first()
    if not user:
        raise CustomException(400, "Invalid or expired token")
    if user.resetTokenExpiry < datetime.utcnow():
        raise CustomException(400, "Token expired")
    user.password = hash_password(newPassword)
    user.resetToken = None
    user.resetTokenExpiry = None
    db.commit()
    return {"success": True, "message": "Password reset successful"}

# CHANGE PASSWORD
def change_password_service(db, user_id, currentPassword, newPassword, confirmPassword):
    if newPassword != confirmPassword:
        raise CustomException(400, "Passwords do not match")
    user = get_customer_by_id(db, user_id)
    if not user:
        raise CustomException(404, "User not found")
    if not verify_password(currentPassword, user.password):
        raise CustomException(401, "Incorrect current password")
    user.password = hash_password(newPassword)
    db.commit()
    return {"success": True, "message": "Password changed successfully"}

# LOGOUT
def logout_customer_service(token: str, current_user):
    TOKEN_BLACKLIST.add(token)
    return {"success": True, "message": "Logged out successfully"}

# GET PROFILE
def get_profile_service(db, cust_id):
    user = get_customer_by_id(db, cust_id)
    if not user:
        raise CustomException(404, "User not found")
    return {
        "id": user.id,
        "firstName": user.firstName,
        "lastName": user.lastName,
        "email": user.email,
        "mobileNumber": user.mobileNumber
    }

# UPDATE PROFILE
def update_profile_service(db, cust_id, data):
    user = get_customer_by_id(db, cust_id)
    if not user:
        raise CustomException(404, "User not found")
    allowed_fields = {
        "firstName",
        "lastName",
        "mobileNumber",
        "profileImage"
    }
    clean_data = {
        k: v for k, v in data.items()
        if k in allowed_fields and v is not None
    }
    for k, v in clean_data.items():
        setattr(user, k, v)
    db.commit()
    db.refresh(user)
    return {
        "message": "Profile updated successfully"
    }


def get_public_listings_service(
    db: Session,
    search,
    category,
    listingType,
    city,
    priceMin,
    priceMax,
    page,
    limit,
    sortBy
):

    try:

        skip = (page - 1) * limit

        total, listings = get_public_listings_repo(
            db=db,
            search=search,
            category=category,
            listingType=listingType,
            city=city,
            priceMin=priceMin,
            priceMax=priceMax,
            skip=skip,
            limit=limit,
            sortBy=sortBy
        )

        return {
            "success": True,
            "message": "Public listings fetched successfully",
            "total": total,
            "page": page,
            "limit": limit,
            "data": listings
        }

    except Exception as e:

        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )
    
def get_public_listing_details_service(
    db: Session,
    listingId
):

    try:

        listing = get_public_listing_details_repo(
            db=db,
            listingId=listingId
        )

        if not listing:

            raise CustomException(
                status.HTTP_404_NOT_FOUND,
                "Listing not found"
            )

        return {
            "success": True,
            "message": "Listing details fetched successfully",
            "data": listing
        }
    except CustomException  as e:
        raise e
    except Exception as e:
        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )
    
def search_listings_service(
    db: Session,
    keyword,
    category,
    listingType,
    location,
    rating,
    sort
):

    try:

        total, listings = search_listings_repo(
            db=db,
            keyword=keyword,
            category=category,
            listingType=listingType,
            location=location,
            rating=rating,
            sort=sort
        )

        return {
            "success": True,
            "message": "Listings fetched successfully",
            "total": total,
            "data": listings
        }

    except Exception as e:
        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )
    
def get_categories_service(db):

    try:

        total, categories = get_categories_repo(db)

        return {
            "success": True,
            "message": "Categories fetched successfully",
            "total": total,
            "data": categories
        }

    except Exception as e:
        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )
    
# GET SUBCATEGORIES
def get_subcategories_service(
    db,
    categoryId
):

    try:

        subcategories = get_subcategories_repo(
            db=db,
            categoryId=categoryId
        )

        return {
            "success": True,
            "message": "Subcategories fetched successfully",
            "data": subcategories
        }

    except Exception as e:
        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )