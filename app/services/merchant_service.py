# app/services/merchant_service.py

from fastapi import (
    UploadFile,
    status,
    HTTPException
)

from sqlalchemy.orm import Session

from app.repository.merchant_repo import (
    get_merchant_by_email,
    get_merchant_by_external_auth_id,
    create_merchant,
    update_merchant,
    get_merchant_by_id,
    create_business_profile,
    get_business_profile_by_merchant_id,
    create_business_draft,
    update_business_profile,
    update_business_draft,
    get_business_draft_by_merchant_id,
    create_listing_repo,
    save_listing_draft_repo,
    get_my_listings_repo,
    get_listing_details_repo,
    get_listing_by_id_repo,
    update_listing_repo,
    delete_listing_repo,
    publish_listing_repo,
    unpublish_listing_repo,
    upload_listing_images_repo,
    delete_listing_image_repo
)

from app.models.merchant_model import (
    Merchant,
    MerchantProfile,
    MerchantBusinessDraft
)

from app.models.admin_model import Business

from app.exceptions.custom_exception import (
    CustomException
)

from app.core.security import hash_password, verify_password, create_access_token, create_refresh_token
from app.services.email_service import send_email
from app.core.token_blacklist import TOKEN_BLACKLIST

from datetime import datetime, timezone, timedelta

from typing import List

import os
import uuid
import secrets
import requests

from uuid import uuid4

GOOGLE_VERIFY_URL = "https://oauth2.googleapis.com/tokeninfo"


# =========================================================
# MERCHANT AUTH
# =========================================================

def register_merchant_service(db: Session, payload):
    existing = get_merchant_by_email(db, payload.businessEmail)
    if existing:
        raise CustomException(400, "Email already registered")
    if payload.password != payload.confirmPassword:
        raise CustomException(400, "Passwords do not match")
    if not payload.acceptTerms or not payload.acceptPrivacyPolicy:
        raise CustomException(400, "Accept terms and privacy policy")
    data = payload.dict()
    data.pop("confirmPassword")
    data["id"] = uuid4()
    data["password"] = hash_password(data["password"])
    merchant = Merchant(**data)
    create_merchant(db, merchant)
    return {"success": True, "message": "Registration successful"}


def login_merchant_service(db: Session, email: str, password: str):
    merchant = get_merchant_by_email(db, email)
    if not merchant:
        raise CustomException(404, "Merchant not found")
    if not verify_password(password, merchant.password):
        raise CustomException(401, "Invalid credentials")
    token_data = {"id": str(merchant.id), "email": merchant.businessEmail, "role": "merchant"}
    return {
        "success": True,
        "message": "Login successful",
        "accessToken": create_access_token(token_data),
        "refreshToken": create_refresh_token(token_data),
        "tokenType": "bearer"
    }


def google_login_service(db: Session, google_token: str):
    res = requests.get(GOOGLE_VERIFY_URL, params={"id_token": google_token})
    if res.status_code != 200:
        raise CustomException(401, "Invalid Google token")
    data = res.json()
    email = data.get("email")
    name = data.get("name", "")
    merchant = get_merchant_by_email(db, email)
    if not merchant:
        merchant = Merchant(
            id=uuid4(),
            fullName=name,
            businessEmail=email,
            businessName=name,
            mobileNumber="",
            password=hash_password(secrets.token_urlsafe(16)),
            acceptTerms=True,
            acceptPrivacyPolicy=True
        )
        create_merchant(db, merchant)
    token_data = {"id": str(merchant.id), "email": merchant.businessEmail, "role": "merchant"}
    return {
        "success": True,
        "accessToken": create_access_token(token_data),
        "refreshToken": create_refresh_token(token_data),
        "tokenType": "bearer"
    }


def forgot_password_merchant_service(db: Session, email: str):
    merchant = get_merchant_by_email(db, email)
    if not merchant:
        raise CustomException(404, "Merchant not found")
    reset_token = secrets.token_urlsafe(32)
    merchant.resetToken = reset_token
    merchant.resetTokenExpiry = datetime.utcnow() + timedelta(minutes=15)
    db.commit()
    reset_link = f"http://localhost:8000/reset-password?token={reset_token}"
    if not send_email(merchant.businessEmail, reset_link):
        raise CustomException(500, "Failed to send email")
    return {"success": True, "message": "Reset link sent successfully"}


def reset_password_merchant_service(db: Session, resetToken: str, newPassword: str, confirmPassword: str):
    if newPassword != confirmPassword:
        raise CustomException(400, "Passwords do not match")
    merchant = db.query(Merchant).filter(Merchant.resetToken == resetToken).first()
    if not merchant:
        raise CustomException(400, "Invalid or expired token")
    if merchant.resetTokenExpiry < datetime.utcnow():
        raise CustomException(400, "Token expired")
    merchant.password = hash_password(newPassword)
    merchant.resetToken = None
    merchant.resetTokenExpiry = None
    db.commit()
    return {"success": True, "message": "Password reset successful"}


def change_password_merchant_service(db: Session, merchant_id: str, currentPassword: str, newPassword: str, confirmPassword: str):
    if newPassword != confirmPassword:
        raise CustomException(400, "Passwords do not match")
    merchant = get_merchant_by_id(db, merchant_id)
    if not merchant:
        raise CustomException(404, "Merchant not found")
    if not verify_password(currentPassword, merchant.password):
        raise CustomException(401, "Incorrect current password")
    merchant.password = hash_password(newPassword)
    db.commit()
    return {"success": True, "message": "Password changed successfully"}


def logout_merchant_service(token: str, current_user):
    TOKEN_BLACKLIST.add(token)
    return {"success": True, "message": "Logged out successfully"}


def _assert_business_owned(
    db: Session,
    business_id,
    merchant_id: str
):
    business = db.query(Business).filter(
        Business.id == business_id
    ).first()

    if not business:
        raise CustomException(
            status.HTTP_404_NOT_FOUND,
            "Business not found"
        )

    if str(business.merchant_id) != str(merchant_id):
        raise CustomException(
            status.HTTP_403_FORBIDDEN,
            "Unauthorized access"
        )

    return business

# =========================================================
# CONSTANTS
# =========================================================

LOGO_FOLDER = "uploads/business_logo"
BANNER_FOLDER = "uploads/business_banner"
GALLERY_FOLDER = "uploads/business_gallery"
UPLOAD_DIR = "uploads/listings"


class BusinessStatus:
    DRAFT = "draft"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUSPENDED = "suspended"


# =========================================================
# MERCHANT PROFILE
# =========================================================

def sync_merchant_service(
    db: Session,
    current_user
):
    """
    Create merchant record if not exists.
    Authentication handled externally.
    """

    external_auth_id = current_user.get("user_id")
    email = current_user.get("email")

    merchant = get_merchant_by_external_auth_id(
        db,
        external_auth_id
    )

    if merchant:
        return merchant

    merchant = Merchant(
        id=str(uuid4()),
        externalAuthUserId=external_auth_id,
        businessEmail=email,
        status="active"
    )

    return create_merchant(db, merchant)


def get_merchant_profile_service(
    db: Session,
    merchant_id: str
):

    merchant = get_merchant_by_id(
        db,
        merchant_id
    )

    if not merchant:

        raise CustomException(
            status.HTTP_404_NOT_FOUND,
            "Merchant not found"
        )

    return {
        "success": True,
        "data": {
            "id": merchant.id,
            "fullName": merchant.fullName,
            "businessName": merchant.businessName,
            "businessEmail": merchant.businessEmail,
            "mobileNumber": merchant.mobileNumber,
            "status": merchant.status
        }
    }


def update_merchant_profile_service(
    db: Session,
    merchant_id: str,
    data
):

    merchant = get_merchant_by_id(
        db,
        merchant_id
    )

    if not merchant:

        raise CustomException(
            status.HTTP_404_NOT_FOUND,
            "Merchant not found"
        )

    allowed_fields = {
        "fullName",
        "businessName",
        "mobileNumber",
        "profileImage"
    }

    for field, value in data.items():

        if (
            field in allowed_fields
            and value is not None
        ):
            setattr(merchant, field, value)

    updated = update_merchant(
        db,
        merchant
    )

    return {
        "success": True,
        "message": "Profile updated successfully",
        "data": updated
    }


# =========================================================
# BUSINESS PROFILE
# =========================================================

def create_business_profile_service(
    db: Session,
    merchant_id: str,
    payload
):

    merchant = get_merchant_by_id(
        db,
        merchant_id
    )

    if not merchant:

        raise CustomException(
            404,
            "Merchant not found"
        )

    existing_profile = get_business_profile_by_merchant_id(
        db,
        merchant_id
    )

    if existing_profile:

        raise CustomException(
            400,
            "Business profile already exists"
        )

    business_type_allowed = [
        "physical",
        "online",
        "hybrid"
    ]

    if payload.businessType not in business_type_allowed:

        raise CustomException(
            400,
            "Invalid business type"
        )

    data = payload.dict()

    profile = MerchantProfile(
        id=str(uuid4()),
        merchant_id=merchant_id,
        status=BusinessStatus.DRAFT,
        **data
    )

    created = create_business_profile(
        db,
        profile
    )

    return {
        "success": True,
        "message": "Business profile created successfully",
        "data": created
    }


def save_business_draft_service(
    db: Session,
    merchant_id: str,
    payload
):

    merchant = get_merchant_by_id(
        db,
        merchant_id
    )

    if not merchant:

        raise CustomException(
            404,
            "Merchant not found"
        )

    existing_draft = get_business_draft_by_merchant_id(
        db,
        merchant_id
    )

    data = payload.dict(
        exclude_unset=True
    )

    if existing_draft:

        for field, value in data.items():
            setattr(existing_draft, field, value)

        updated = update_business_draft(
            db,
            existing_draft
        )

        return {
            "success": True,
            "message": "Draft updated successfully",
            "data": updated
        }

    draft = MerchantBusinessDraft(
        id=str(uuid4()),
        merchant_id=merchant_id,
        **data
    )

    created = create_business_draft(
        db,
        draft
    )

    return {
        "success": True,
        "message": "Draft saved successfully",
        "data": created
    }


def get_business_profile_service(
    db: Session,
    merchant_id: str
):

    profile = get_business_profile_by_merchant_id(
        db,
        merchant_id
    )

    if not profile:

        raise CustomException(
            404,
            "Business profile not found"
        )

    return {
        "success": True,
        "data": profile
    }


def update_business_profile_service(
    db: Session,
    merchant_id: str,
    payload
):

    profile = get_business_profile_by_merchant_id(
        db,
        merchant_id
    )

    if not profile:

        raise CustomException(
            404,
            "Business profile not found"
        )

    data = payload.dict(
        exclude_unset=True
    )

    restricted_fields = {
        "businessName",
        "fullAddress",
        "city",
        "state",
        "zipCode",
        "country",
        "latitude",
        "longitude",
        "primaryCategory",
        "subcategory"
    }

    reapproval_required = False

    for field, value in data.items():

        if hasattr(profile, field):

            setattr(profile, field, value)

            if field in restricted_fields:
                reapproval_required = True

    if reapproval_required:
        profile.status = BusinessStatus.PENDING

    updated = update_business_profile(
        db,
        profile
    )

    return {
        "success": True,
        "message": "Business profile updated successfully",
        "reapprovalRequired": reapproval_required,
        "data": updated
    }


def submit_business_for_approval_service(
    db: Session,
    merchant_id: str
):

    profile = get_business_profile_by_merchant_id(
        db,
        merchant_id
    )

    if not profile:

        raise CustomException(
            404,
            "Business profile not found"
        )

    if profile.status == BusinessStatus.PENDING:

        raise CustomException(
            400,
            "Business already submitted"
        )

    required_fields = [
        "businessName",
        "primaryCategory",
        "businessEmail",
        "phoneNumber",
        "fullAddress",
        "city",
        "state",
        "zipCode",
        "country",
        "businessType"
    ]

    for field in required_fields:

        if not getattr(profile, field):

            raise CustomException(
                400,
                f"{field} is required"
            )

    profile.status = BusinessStatus.PENDING

    update_business_profile(
        db,
        profile
    )

    existing_business = db.query(Business).filter(
        Business.merchant_id == merchant_id
    ).first()

    if existing_business:

        existing_business.name = profile.businessName
        existing_business.category = profile.primaryCategory
        existing_business.status = BusinessStatus.PENDING

        db.commit()
        db.refresh(existing_business)

        business = existing_business

    else:

        business = Business(
            id=str(uuid4()),
            merchant_id=merchant_id,
            name=profile.businessName,
            category=profile.primaryCategory,
            status=BusinessStatus.PENDING
        )

        db.add(business)
        db.commit()
        db.refresh(business)

    return {
        "success": True,
        "message": "Business submitted for approval",
        "businessId": business.id,
        "status": business.status
    }


# =========================================================
# BUSINESS STATUS
# =========================================================

def get_business_status_service(
    db: Session,
    merchant_id: str
):

    profile = get_business_profile_by_merchant_id(
        db,
        merchant_id
    )

    if not profile:

        raise CustomException(
            404,
            "Business profile not found"
        )

    return {
        "success": True,
        "businessStatus": profile.status,
        "businessName": profile.businessName
    }


# =========================================================
# LISTINGS
# =========================================================

def get_listing_details_service(
    db: Session,
    listingId
):
    listing = get_listing_by_id_repo(db=db, listingId=listingId)
    if not listing:
        raise CustomException(404, "Listing not found")
    return {"success": True, "data": listing}


def _save_upload(folder: str, file: UploadFile) -> str:
    os.makedirs(folder, exist_ok=True)
    ext = file.filename.split(".")[-1]
    filename = f"{uuid.uuid4()}.{ext}"
    path = os.path.join(folder, filename)
    with open(path, "wb") as f:
        f.write(file.file.read())
    return filename


def upload_business_logo_service(db: Session, merchant_id: str, file: UploadFile):
    profile = get_business_profile_by_merchant_id(db, merchant_id)
    if not profile:
        raise CustomException(404, "Business profile not found")
    if not file.content_type.startswith("image/"):
        raise CustomException(400, "Invalid image file")
    filename = _save_upload(LOGO_FOLDER, file)
    profile.businessLogo = filename
    update_business_profile(db, profile)
    return {"success": True, "message": "Logo uploaded successfully", "businessLogo": filename}


def upload_business_banner_service(db: Session, merchant_id: str, file: UploadFile):
    profile = get_business_profile_by_merchant_id(db, merchant_id)
    if not profile:
        raise CustomException(404, "Business profile not found")
    if not file.content_type.startswith("image/"):
        raise CustomException(400, "Invalid image file")
    filename = _save_upload(BANNER_FOLDER, file)
    profile.bannerImage = filename
    update_business_profile(db, profile)
    return {"success": True, "message": "Banner uploaded successfully", "bannerImage": filename}


def upload_business_gallery_service(db: Session, merchant_id: str, files: list):
    profile = get_business_profile_by_merchant_id(db, merchant_id)
    if not profile:
        raise CustomException(404, "Business profile not found")
    uploaded = []
    for file in files:
        if not file.content_type.startswith("image/"):
            raise CustomException(400, f"{file.filename} is not a valid image")
        uploaded.append(_save_upload(GALLERY_FOLDER, file))
    existing = profile.galleryImages or []
    profile.galleryImages = existing + uploaded
    update_business_profile(db, profile)
    return {"success": True, "message": "Gallery images uploaded", "galleryImages": profile.galleryImages}


def delete_business_gallery_image_service(db: Session, merchant_id: str, image_id: str):
    profile = get_business_profile_by_merchant_id(db, merchant_id)
    if not profile:
        raise CustomException(404, "Business profile not found")
    images = profile.galleryImages or []
    if image_id not in images:
        raise CustomException(404, "Image not found")
    profile.galleryImages = [img for img in images if img != image_id]
    image_path = os.path.join(GALLERY_FOLDER, image_id)
    if os.path.exists(image_path):
        os.remove(image_path)
    update_business_profile(db, profile)
    return {"success": True, "message": "Image deleted successfully"}


def create_listing_service(
    db: Session,
    merchant_id: str,
    payload
):

    try:

        listing = create_listing_repo(
            db=db,
            merchant_id=merchant_id,
            payload=payload
        )

        return {
            "success": True,
            "message": "Listing created successfully",
            "data": listing
        }

    except HTTPException:
        raise

    except Exception:

        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Internal server error"
        )

def save_listing_draft_service(
    db: Session,
    merchant_id: str,
    payload
):
    try:
        _assert_business_owned(
            db=db,
            business_id=payload.businessId,
            merchant_id=merchant_id
        )

        draft = save_listing_draft_repo(
            db=db,
            payload=payload
        )

        return {
            "success": True,
            "message": "Listing draft saved successfully",
            "data": draft
        }

    except CustomException:
        raise
    except Exception:
        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Internal server error"
        )

def get_my_listings_service(
    db: Session,
    merchant_id,
    businessId,
    status_filter,
    listingType,
    search,
    page,
    limit
):

    try:

        skip = (page - 1) * limit

        total, listings = get_my_listings_repo(
            db=db,
            merchant_id=merchant_id,
            businessId=businessId,
            status=status_filter,
            listingType=listingType,
            search=search,
            skip=skip,
            limit=limit
        )

        return {
            "success": True,
            "total": total,
            "page": page,
            "limit": limit,
            "data": listings
        }

    except Exception:

        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Internal server error"
        )


def update_listing_service(
    db: Session,
    merchant_id,
    listingId,
    payload
):

    listing = get_listing_by_id_repo(
        db=db,
        listingId=listingId
    )

    if not listing:

        raise CustomException(
            404,
            "Listing not found"
        )

    _assert_business_owned(
        db=db,
        business_id=listing.businessId,
        merchant_id=merchant_id
    )

    updated = update_listing_repo(
        db=db,
        listing=listing,
        payload=payload
    )

    return {
        "success": True,
        "message": "Listing updated successfully",
        "data": updated
    }


def delete_listing_service(
    db: Session,
    merchant_id,
    listingId
):

    listing = get_listing_by_id_repo(
        db=db,
        listingId=listingId
    )

    if not listing:

        raise CustomException(
            404,
            "Listing not found"
        )

    _assert_business_owned(
        db=db,
        business_id=listing.businessId,
        merchant_id=merchant_id
    )

    delete_listing_repo(
        db=db,
        listing=listing
    )

    return {
        "success": True,
        "message": "Listing deleted successfully"
    }


def publish_listing_service(
    db: Session,
    merchant_id,
    listingId
):

    listing = get_listing_by_id_repo(
        db=db,
        listingId=listingId
    )

    if not listing:

        raise CustomException(
            404,
            "Listing not found"
        )

    _assert_business_owned(
        db=db,
        business_id=listing.businessId,
        merchant_id=merchant_id
    )

    updated = publish_listing_repo(
        db=db,
        listing=listing
    )

    return {
        "success": True,
        "message": "Listing published successfully",
        "data": updated
    }


def unpublish_listing_service(
    db: Session,
    merchant_id,
    listingId
):

    listing = get_listing_by_id_repo(
        db=db,
        listingId=listingId
    )

    if not listing:

        raise CustomException(
            404,
            "Listing not found"
        )

    _assert_business_owned(
        db=db,
        business_id=listing.businessId,
        merchant_id=merchant_id
    )

    updated = unpublish_listing_repo(
        db=db,
        listing=listing
    )

    return {
        "success": True,
        "message": "Listing unpublished successfully",
        "data": updated
    }

def upload_listing_images_service(
    db: Session,
    listingId,
    files
):
    try:
        # CHECK LISTING EXISTS
        listing = get_listing_by_id_repo(
            db=db,
            listingId=listingId
        )
        if not listing:
            raise CustomException(
                status.HTTP_404_NOT_FOUND,
                "Listing not found"
            )
        # CREATE UPLOAD DIRECTORY
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        uploaded_images = []
        # SAVE FILES
        for file in files:
            # VALIDATE IMAGE TYPE
            if not file.content_type.startswith("image/"):
                raise CustomException(
                    status.HTTP_400_BAD_REQUEST,
                    f"{file.filename} is not a valid image"
                )
            # GENERATE UNIQUE FILE NAME
            file_extension = file.filename.split(".")[-1]
            unique_filename = (
                f"{uuid.uuid4()}.{file_extension}"
            )
            file_path = os.path.join(
                UPLOAD_DIR,
                unique_filename
            )
            # SAVE FILE
            with open(file_path, "wb") as image:
                image.write(file.file.read())
            uploaded_images.append(unique_filename)
        # UPDATE DATABASE
        updated_listing = upload_listing_images_repo(
            db=db,
            listing=listing,
            uploaded_images=uploaded_images
        )
        return {
            "success": True,
            "message": "Listing images uploaded successfully",
            "data": {
                "listingId": updated_listing.id,
                "images": updated_listing.images
            }
        }
    except CustomException  as e:
        raise e
    except Exception as e:
        db.rollback()
        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

def delete_listing_image_service(
    db: Session,
    listingId,
    imageId
):
    try:
        # CHECK LISTING EXISTS
        listing = get_listing_by_id_repo(
            db=db,
            listingId=listingId
        )
        if not listing:
            raise CustomException(
                status.HTTP_404_NOT_FOUND,
                "Listing not found"
            )
        images = listing.images or []
        # CHECK IMAGE EXISTS
        if imageId not in images:
            raise CustomException(
                status.HTTP_404_NOT_FOUND,
                "Image not found in listing"
            )
        # REMOVE IMAGE FROM ARRAY
        updated_images = [
            image for image in images
            if image != imageId
        ]
        # DELETE IMAGE FILE
        image_path = os.path.join(
            UPLOAD_DIR,
            imageId
        )
        if os.path.exists(image_path):
            os.remove(image_path)
        # UPDATE DATABASE
        updated_listing = delete_listing_image_repo(
            db=db,
            listing=listing,
            updated_images=updated_images
        )
        return {
            "success": True,
            "message": "Listing image deleted successfully",
            "data": {
                "listingId": updated_listing.id,
                "deletedImage": imageId,
                "remainingImages": updated_listing.images
            }
        }
    except CustomException  as e:
        raise e
    except Exception as e:
        db.rollback()
        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )