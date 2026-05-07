from fastapi import UploadFile, status, HTTPException
from app.repository.merchant_repo import (
    get_merchant_by_email, 
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
from sqlalchemy.orm import Session
from app.models.merchant_model import Merchant, MerchantProfile, MerchantBusinessDraft
from app.models.admin_model import Business
from app.exceptions.custom_exception import CustomException
from datetime import datetime, timedelta
from app.core.security import hash_password, verify_password, create_access_token, create_refresh_token
from app.services.email_service import send_email 
from app.core.token_blacklist import TOKEN_BLACKLIST
from typing import List
import os
import uuid
from uuid import uuid4
import requests
import secrets

GOOGLE_VERIFY_URL = "https://oauth2.googleapis.com/tokeninfo"
LOGO_FOLDER = "uploads/business_logo"
BANNER_FOLDER = "uploads/business_banner"
GALLERY_FOLDER = "uploads/business_gallery"
UPLOAD_DIR = "uploads/listings"

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

    # Check merchant exists
    merchant = get_merchant_by_email(db, email)

    if not merchant:
        raise CustomException(404, "Merchant not found")

    # Verify password
    if not verify_password(password, merchant.password):
        raise CustomException(401, "Invalid credentials")

    token_data = {
        "sub": str(merchant.id),
        "email": merchant.businessEmail,
        "role": "merchant"
    }

    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    return {
        "success": True,
        "message": "Merchant login successful",
        "access_token": access_token,
        "refresh_token": refresh_token,
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

# Business Profile
def create_business_profile_service(db, merchant_id: str, payload):

    merchant = get_merchant_by_id(db, merchant_id)

    if not merchant:
        raise CustomException(404, "Merchant not found")

    existing_profile = get_business_profile_by_merchant_id(db, merchant_id)

    if existing_profile:
        raise CustomException(400, "Business profile already exists")

    if payload.businessType not in ["physical", "online", "hybrid"]:
        raise CustomException(400, "Invalid business type")

    data = payload.dict()
    data["id"] = str(uuid4())
    data["merchant_id"] = merchant_id

    profile = MerchantProfile(**data)

    return create_business_profile(db, profile)

def save_business_draft_service(db, merchant_id, payload):

    merchant = get_merchant_by_id(db, merchant_id)

    if not merchant:
        raise CustomException(404, "Merchant not found")

    existing_draft = get_business_draft_by_merchant_id(
        db,
        merchant_id
    )

    data = payload.dict(exclude_unset=True)

    # UPDATE EXISTING DRAFT
    if existing_draft:

        for field, value in data.items():
            setattr(existing_draft, field, value)

        update_business_draft(db, existing_draft)

        return {
            "success": True,
            "message": "Business draft updated successfully",
            "data": existing_draft
        }

    # CREATE NEW DRAFT
    new_draft = MerchantBusinessDraft(
        id=str(uuid4()),
        merchant_id=merchant_id,
        **data
    )

    create_business_draft(db, new_draft)

    return {
        "success": True,
        "message": "Business draft saved successfully",
        "data": new_draft
    }

def get_business_profile_service(
    db,
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
        "message": "Business profile fetched successfully",
        "data": profile
    }

def update_business_profile_service(
    db,
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

    data = payload.dict(exclude_unset=True)

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
      # If restricted changes made
    if reapproval_required:
        profile.status = "pending_approval"

    updated = update_business_profile(
        db,
        profile
    )

    return {
        "success": True,
        "message": (
            "Business updated successfully. "
            "Re-approval required."
            if reapproval_required
            else
            "Business updated successfully"
        ),
        "reapproval_required": reapproval_required,
        "data": updated
    }

def submit_business_for_approval_service(db, merchant_id: str):

    profile = get_business_profile_by_merchant_id(db, merchant_id)
    if not profile:
        raise CustomException(404, "Business profile not found")
    if profile.status == "pending_approval":
        raise CustomException(400, "Business already submitted")
    if profile.status == "approved":
        raise CustomException(400, "Business already approved")
    # Validate required fields
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
            raise CustomException(400, f"{field} is required")
    # STEP 1: Update profile status
    profile.status = "pending_approval"
    update_business_profile(db, profile)
    # STEP 2: CREATE BUSINESS (THIS WAS MISSING)
    new_business = Business(
        name=profile.businessName,
        category=profile.primaryCategory,
        merchant_id=merchant_id,
        status="pending"
    )
    db.add(new_business)
    db.commit()
    db.refresh(new_business)
    return {
        "success": True,
        "message": "Business submitted for approval",
        "business_id": str(new_business.id),
        "status": new_business.status
    }

def upload_business_logo_service(
    db,
    merchant_id: str,
    file: UploadFile
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

    if not file:
        raise CustomException(
            400,
            "File is required"
        )

    # Validate file type
    allowed_extensions = [
        ".jpg",
        ".jpeg",
        ".png",
        ".webp"
    ]

    ext = os.path.splitext(file.filename)[1].lower()

    if ext not in allowed_extensions:
        raise CustomException(
            400,
            "Only jpg, jpeg, png, webp files allowed"
        )

    # Create upload folder
    os.makedirs(LOGO_FOLDER, exist_ok=True)

    filename = f"{uuid.uuid4()}{ext}"
    filepath = os.path.join(
        LOGO_FOLDER,
        filename
    )

    with open(filepath, "wb") as buffer:
        buffer.write(file.file.read())

    # Save path in DB
    profile.businessLogo = filepath

    update_business_profile(
        db,
        profile
    )

    return {
        "success": True,
        "message": "Business logo uploaded successfully",
        "logo": filepath
    }


def upload_business_banner_service(
    db,
    merchant_id: str,
    file: UploadFile
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

    if not file:
        raise CustomException(
            400,
            "File is required"
        )

   
    # Validate file type
    allowed_extensions = [
        ".jpg",
        ".jpeg",
        ".png",
        ".webp"
    ]

    ext = os.path.splitext(file.filename)[1].lower()

    if ext not in allowed_extensions:
        raise CustomException(
            400,
            "Only jpg, jpeg, png, webp files allowed"
        )

    # Create upload folder
    os.makedirs(BANNER_FOLDER, exist_ok=True)

    filename = f"{uuid.uuid4()}{ext}"
    filepath = os.path.join(
        BANNER_FOLDER,
        filename
    )

    with open(filepath, "wb") as buffer:
        buffer.write(file.file.read())

    # Save path in DB
    profile.bannerImage = filepath

    update_business_profile(
        db,
        profile
    )

    return {
        "success": True,
        "message": "Business banner uploaded successfully",
        "banner": filepath
    }




def upload_business_gallery_service(
    db,
    merchant_id: str,
    files: List[UploadFile]
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

    if not files:
        raise CustomException(
            400,
            "Files are required"
        )

    allowed_extensions = [
        ".jpg",
        ".jpeg",
        ".png",
        ".webp"
    ]

    os.makedirs(
        GALLERY_FOLDER,
        exist_ok=True
    )

    uploaded_files = []

    for file in files:

        ext = os.path.splitext(
            file.filename
        )[1].lower()

        if ext not in allowed_extensions:
            raise CustomException(
                400,
                f"Invalid file type: {file.filename}"
            )

        filename = f"{uuid.uuid4()}{ext}"

        filepath = os.path.join(
            GALLERY_FOLDER,
            filename
        )

        with open(filepath, "wb") as buffer:
            buffer.write(file.file.read())

        uploaded_files.append(filepath)

    # Save / append gallery images
    existing_images = profile.galleryImages or []

    profile.galleryImages = (
        existing_images + uploaded_files
    )

    update_business_profile(
        db,
        profile
    )

    return {
        "success": True,
        "message": "Gallery images uploaded successfully",
        "galleryImages": profile.galleryImages
    }

def delete_business_gallery_image_service(
    db,
    merchant_id: str,
    image_id: str
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

    gallery_images = profile.galleryImages or []

    if not gallery_images:
        raise CustomException(
            404,
            "No gallery images found"
        )

    # image_id expected as filename
    # Example: 9e7f3a2b.png
    image_path = None

    for img in gallery_images:
        filename = os.path.basename(img)

        if filename == image_id:
            image_path = img
            break

    if not image_path:
        raise CustomException(
            404,
            "Image not found"
        )

    # Remove from filesystem
    if os.path.exists(image_path):
        os.remove(image_path)

    # Remove from DB list
    updated_images = [
        img for img in gallery_images
        if os.path.basename(img) != image_id
    ]

    profile.galleryImages = updated_images

    update_business_profile(
        db,
        profile
    )

    return {
        "success": True,
        "message": "Gallery image deleted successfully",
        "galleryImages": updated_images
    }

def get_business_status_service(
    db,
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
        "message": "Business status fetched successfully",
        "businessStatus": profile.status,
        "businessName": profile.businessName
    }



def create_listing_service(db: Session, payload):

    try:

        # PRODUCT VALIDATION
        if payload.listingType == "product":

            if payload.stockQuantity is None:
                raise CustomException(
                    status.HTTP_400_BAD_REQUEST,
                    "stockQuantity is required for product"
                )

            if payload.sku is None:
                raise CustomException(
                    status.HTTP_400_BAD_REQUEST,
                    "sku is required for product"
                )

        # SERVICE VALIDATION
        elif payload.listingType == "service":

            if payload.duration is None:
                raise CustomException(
                    status.HTTP_400_BAD_REQUEST,
                    "duration is required for service"
                )

            if payload.serviceMode is None:
                raise CustomException(
                    status.HTTP_400_BAD_REQUEST,
                    "serviceMode is required for service"
                )

        # EVENT / TRAINING / PROGRAM VALIDATION
        elif payload.listingType in ["event", "training", "program"]:

            required_fields = {
                "startDate": payload.startDate,
                "endDate": payload.endDate,
                "capacity": payload.capacity,
                "location": payload.location,
                "registrationDeadline": payload.registrationDeadline
            }

            for field_name, value in required_fields.items():

                if value is None:
                    raise CustomException(
                        status.HTTP_400_BAD_REQUEST,
                        f"{field_name} is required for {payload.listingType}"
                    )

        listing = create_listing_repo(db, payload)

        return {
            "success": True,
            "message": "Listing created successfully",
            "data": listing
        }

    except HTTPException as e:
        raise e

    except Exception as e:
        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )
    

def save_listing_draft_service(
    db: Session,
    payload
):
    try:
        draft = save_listing_draft_repo(
            db=db,
            payload=payload
        )
        return {
            "success": True,
            "message": "Listing saved as draft successfully",
            "data": draft
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        db.rollback()
        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

def get_my_listings_service(
    db: Session,
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
            businessId=businessId,
            status=status_filter,
            listingType=listingType,
            search=search,
            skip=skip,
            limit=limit
        )
        return {
            "success": True,
            "message": "Listings fetched successfully",
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
    
def get_listing_details_service(
    db: Session,
    listingId
):
    try:
        listing = get_listing_details_repo(
            db=db,
            listingId=listingId
        )
        # LISTING NOT FOUND
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
    except HTTPException as e:
        raise e
    except Exception as e:
        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

def update_listing_service(
    db: Session,
    listingId,
    payload
):
    try:
        # CHECK LISTING EXISTS
        listing = get_listing_by_id_repo(
            db=db,
            listingId=listingId
        )
        if not listing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Listing not found"
            )
        # UPDATE LISTING
        updated_listing = update_listing_repo(
            db=db,
            listing=listing,
            payload=payload
        )
        return {
            "success": True,
            "message": "Listing updated successfully",
            "data": updated_listing
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        db.rollback()
        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )
    
def delete_listing_service(
    db: Session,
    listingId
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
        # DELETE LISTING
        delete_listing_repo(
            db=db,
            listing=listing
        )
        return {
            "success": True,
            "message": "Listing deleted successfully",
            "deletedListingId": listingId
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        db.rollback()
        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

def publish_listing_service(
    db: Session,
    listingId
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
        # CHECK IF ALREADY PUBLISHED
        if listing.status == "published":
            raise CustomException(
                status.HTTP_400_BAD_REQUEST,
                "Listing already published"
            )
        # VALIDATE REQUIRED FIELDS BEFORE PUBLISH
        required_fields = {
            "title": listing.title,
            "description": listing.description,
            "listingType": listing.listingType,
            "price": listing.price
        }
        for field_name, value in required_fields.items():
            if value is None or value == "":
                raise CustomException(
                    status.HTTP_400_BAD_REQUEST,
                    f"{field_name} is required to publish listing"
                )
        # PUBLISH LISTING
        updated_listing = publish_listing_repo(
            db=db,
            listing=listing
        )
        return {
            "success": True,
            "message": "Listing published successfully",
            "data": {
                "id": updated_listing.id,
                "status": updated_listing.status,
                "updated_at": updated_listing.updated_at
            }
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        db.rollback()
        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

def unpublish_listing_service(
    db: Session,
    listingId
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
        # CHECK IF ALREADY UNPUBLISHED
        if listing.status == "draft":
            raise CustomException(
                status.HTTP_400_BAD_REQUEST,
                "Listing already unpublished"
            )
        # UNPUBLISH LISTING
        updated_listing = unpublish_listing_repo(
            db=db,
            listing=listing
        )
        return {
            "success": True,
            "message": "Listing unpublished successfully",
            "data": {
                "id": updated_listing.id,
                "status": updated_listing.status,
                "updated_at": updated_listing.updated_at
            }
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        db.rollback()
        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )
    
import os
import uuid

from fastapi import (
    HTTPException,
    UploadFile,
    status
)

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
    except HTTPException as e:
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
    except HTTPException as e:
        raise e
    except Exception as e:
        db.rollback()
        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )