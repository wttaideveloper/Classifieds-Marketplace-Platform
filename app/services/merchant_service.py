# app/services/merchant_service.py

from fastapi import (
    UploadFile,
    status,
    HTTPException
)

from sqlalchemy.orm import Session

from app.repository.merchant_repo import (
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

from datetime import datetime, timezone

from typing import List

import os
import uuid

from uuid import uuid4


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

    if str(listing.merchant_id) != str(merchant_id):

        raise CustomException(
            403,
            "Unauthorized access"
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

    if str(listing.merchant_id) != str(merchant_id):

        raise CustomException(
            403,
            "Unauthorized access"
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

    if str(listing.merchant_id) != str(merchant_id):

        raise CustomException(
            403,
            "Unauthorized access"
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

    if str(listing.merchant_id) != str(merchant_id):

        raise CustomException(
            403,
            "Unauthorized access"
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