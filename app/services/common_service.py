from jose import jwt, JWTError, ExpiredSignatureError
from fastapi import status, HTTPException, UploadFile
from typing import List
from app.core.config import settings
from app.exceptions.custom_exception import CustomException
from app.core.security import create_access_token
from app.models.customer_model import Customer
from app.models.merchant_model import Merchant
from app.models.admin_model import Admin
from app.services.email_service import send_email
from app.repository.customer_repo import get_customer_by_id
from app.repository.merchant_repo import get_merchant_by_id
from app.repository.admin_repo import get_admin_by_id
import secrets
import os
import uuid
from app.exceptions.custom_exception import (
    CustomException
)

UPLOAD_FOLDER = "uploads/business_images"
LISTING_UPLOAD_FOLDER = "uploads/listing_images"
ALLOWED_EXTENSIONS = [
    "jpg",
    "jpeg",
    "png",
    "webp"
]

MAX_FILE_SIZE = 5 * 1024 * 1024


# def refresh_token_service(payload):

#     token = payload.refreshToken

#     try:
#         decoded = jwt.decode(
#             token,
#             settings.SECRET_KEY,
#             algorithms=[settings.ALGORITHM]
#         )

#         if decoded.get("type") != "refresh":
#             raise CustomException(
#                 status.HTTP_401_UNAUTHORIZED,
#                 "Invalid refresh token"
#             )

#         user_data = {
#             "id": decoded.get("id"),
#             "email": decoded.get("email"),
#             "role": decoded.get("role")
#         }

#         access_token = create_access_token(user_data)

#         return {
#             "success": True,
#             "message": "Access token refreshed successfully",
#             "access_token": access_token
#         }

#     except ExpiredSignatureError:
#         raise CustomException(401, "Refresh token expired")

#     except JWTError:
#         raise CustomException(401, "Invalid refresh token")
    

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


# def verify_email_service(db, payload):

#     token = payload.verificationToken

#     if not token:
#         raise CustomException(
#             status.HTTP_400_BAD_REQUEST,
#             "Verification token is required"
#         )

#     user = None

#     # customer
#     user = db.query(Customer).filter(
#         Customer.verificationToken == token
#     ).first()

#     # merchant
#     if not user:
#         user = db.query(Merchant).filter(
#             Merchant.verificationToken == token
#         ).first()

#     # admin
#     if not user:
#         user = db.query(Admin).filter(
#             Admin.verificationToken == token
#         ).first()

#     if not user:
#         raise CustomException(
#             status.HTTP_404_NOT_FOUND,
#             "Invalid verification token"
#         )

#     if user.isEmailVerified:
#         raise CustomException(
#             status.HTTP_400_BAD_REQUEST,
#             "Email already verified"
#         )

#     user.isEmailVerified = True
#     user.verificationToken = None

#     db.commit()
#     db.refresh(user)

#     return {
#         "success": True,
#         "message": "Email verified successfully"
#     }

# def resend_verification_service(db, payload):

#     email = payload.email

#     user = None
#     role = None

#     # CUSTOMER
#     user = db.query(Customer).filter(
#         Customer.email == email
#     ).first()

#     if user:
#         role = "customer"

#     # MERCHANT
#     if not user:
#         user = db.query(Merchant).filter(
#             Merchant.businessEmail == email
#         ).first()

#         if user:
#             role = "merchant"

#     # ADMIN
#     if not user:
#         user = db.query(Admin).filter(
#             Admin.email == email
#         ).first()

#         if user:
#             role = "admin"

#     # NOT FOUND
#     if not user:
#         raise CustomException(404, "User not found")

#     # ALREADY VERIFIED
#     if user.isEmailVerified:
#         raise CustomException(400, "Email already verified")

#     # CREATE TOKEN
#     verification_token = secrets.token_urlsafe(32)

#     user.verificationToken = verification_token

#     db.commit()
#     db.refresh(user)

#     send_email(email, verification_token)

#     return {
#         "success": True,
#         "message": f"Verification email sent successfully to {role}",
#         "verificationToken": verification_token
#     }

def validate_role(role: str):

    allowed_roles = [
        "customer",
        "merchant",
        "admin"
    ]

    if role.lower() not in allowed_roles:

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid role"
        )

    return True

async def upload_business_image_service(
    files: List[UploadFile]
):

    try:

        os.makedirs(
            UPLOAD_FOLDER,
            exist_ok=True
        )

        uploaded_files = []

        for file in files:

            # VALIDATE IMAGE TYPE
            if not file.content_type.startswith("image/"):

                raise CustomException(
                    status.HTTP_400_BAD_REQUEST,
                    f"{file.filename} is not a valid image"
                )

            # READ FILE
            file_content = await file.read()

            # VALIDATE FILE SIZE
            if len(file_content) > MAX_FILE_SIZE:

                raise CustomException(
                    status.HTTP_400_BAD_REQUEST,
                    f"{file.filename} exceeds 5MB limit"
                )

            # VALIDATE EXTENSION
            extension = (
                file.filename.split(".")[-1].lower()
            )

            if extension not in ALLOWED_EXTENSIONS:

                raise CustomException(
                    status.HTTP_400_BAD_REQUEST,
                    f"{file.filename} format not supported"
                )

            # GENERATE UNIQUE NAME
            unique_filename = (
                f"{uuid.uuid4()}.{extension}"
            )

            file_path = os.path.join(
                UPLOAD_FOLDER,
                unique_filename
            )

            # SAVE FILE
            with open(file_path, "wb") as image:
                image.write(file_content)

            uploaded_files.append({
                "fileName": unique_filename,
                "filePath": file_path
            })

        return {
            "success": True,
            "message": "Images uploaded successfully",
            "data": uploaded_files
        }

    except CustomException:
        raise

    except Exception as e:

        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )

async def upload_listing_images_service(
    files: List[UploadFile]
):

    try:

        # CREATE FOLDER
        os.makedirs(
            LISTING_UPLOAD_FOLDER,
            exist_ok=True
        )

        uploaded_images = []

        for file in files:

            # VALIDATE CONTENT TYPE
            if not file.content_type.startswith("image/"):

                raise CustomException(
                    status.HTTP_400_BAD_REQUEST,
                    f"{file.filename} is not a valid image"
                )

            # READ FILE
            file_content = await file.read()

            # VALIDATE FILE SIZE
            if len(file_content) > MAX_FILE_SIZE:

                raise CustomException(
                    status.HTTP_400_BAD_REQUEST,
                    f"{file.filename} exceeds 5MB limit"
                )

            # VALIDATE FILE EXTENSION
            file_extension = (
                file.filename.split(".")[-1].lower()
            )

            if file_extension not in ALLOWED_EXTENSIONS:

                raise CustomException(
                    status.HTTP_400_BAD_REQUEST,
                    f"{file.filename} format not supported"
                )

            # GENERATE UNIQUE FILE NAME
            unique_filename = (
                f"{uuid.uuid4()}.{file_extension}"
            )

            # FILE PATH
            file_path = os.path.join(
                LISTING_UPLOAD_FOLDER,
                unique_filename
            )

            # SAVE FILE
            with open(file_path, "wb") as image:
                image.write(file_content)

            uploaded_images.append({
                "fileName": unique_filename,
                "filePath": file_path
            })

        return {
            "success": True,
            "message": "Listing images uploaded successfully",
            "data": uploaded_images
        }

    except CustomException:
        raise

    except Exception as e:

        raise CustomException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            str(e)
        )