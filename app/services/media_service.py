import os
import uuid
from uuid import UUID

from fastapi import UploadFile, status
from sqlalchemy.orm import Session

from app.repository.media_repo import (
    create_media_repo, 
    get_media_by_id_repo, 
    delete_media_repo, 
    create_business_gallery_repo,
    get_business_by_id_repo,
    get_listing_by_id_repo,
    create_listing_media_repo
)
from app.exceptions.custom_exception import CustomException


UPLOAD_DIRECTORY = "uploads"
BUSINESS_UPLOADS = "uploads/business"
LISTING_UPLOADS = "uploads/listing"

ALLOWED_IMAGE_TYPES = [
    "image/jpeg",
    "image/png",
    "image/webp"
]

ALLOWED_VIDEO_TYPES = [
    "video/mp4",
    "video/quicktime"
]

ALLOWED_DOCUMENT_TYPES = [
    "application/pdf"
]

MAX_IMAGE_SIZE = 5 * 1024 * 1024
MAX_VIDEO_SIZE = 100 * 1024 * 1024
MAX_DOCUMENT_SIZE = 10 * 1024 * 1024


def validate_file(
    file: UploadFile,
    file_size: int
):

    content_type = file.content_type

    if content_type in ALLOWED_IMAGE_TYPES:

        if file_size > MAX_IMAGE_SIZE:
            raise CustomException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Image size exceeds limit"
            )

        return "image"

    elif content_type in ALLOWED_VIDEO_TYPES:

        if file_size > MAX_VIDEO_SIZE:
            raise CustomException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Video size exceeds limit"
            )

        return "video"

    elif content_type in ALLOWED_DOCUMENT_TYPES:

        if file_size > MAX_DOCUMENT_SIZE:
            raise CustomException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Document size exceeds limit"
            )

        return "document"

    raise CustomException(
        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        detail="Unsupported file type"
    )


async def upload_media_service(
    db: Session,
    files: list[UploadFile]
):

    uploaded_files = []

    if not os.path.exists(UPLOAD_DIRECTORY):
        os.makedirs(UPLOAD_DIRECTORY)

    for file in files:

        content = await file.read()

        file_size = len(content)

        file_type = validate_file(
            file=file,
            file_size=file_size
        )

        extension = file.filename.split(".")[-1]

        unique_file_name = f"{uuid.uuid4()}.{extension}"

        file_path = os.path.join(
            UPLOAD_DIRECTORY,
            unique_file_name
        )

        with open(file_path, "wb") as buffer:
            buffer.write(content)

        media_data = {
            "file_name": unique_file_name,
            "original_file_name": file.filename,
            "file_type": file_type,
            "mime_type": file.content_type,
            "file_size": file_size,
            "file_url": file_path
        }

        media = create_media_repo(
            db=db,
            media_data=media_data
        )

        uploaded_files.append(media)

    return uploaded_files

def get_media_by_id_service(
    db: Session,
    media_id: UUID
):

    media = get_media_by_id_repo(
        db=db,
        media_id=media_id
    )

    if not media:
        raise CustomException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media not found"
        )

    return media

def delete_media_service(
    db: Session,
    media_id: UUID
):

    media = get_media_by_id_repo(
        db=db,
        media_id=media_id
    )

    if not media:
        raise CustomException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media not found"
        )

    if os.path.exists(media.file_url):
        os.remove(media.file_url)

    delete_media_repo(
        db=db,
        media=media
    )

    return True




async def upload_business_gallery_service(
    db: Session,
    business_id: UUID,
    files: list[UploadFile]
):

    business = get_business_by_id_repo(
        db=db,
        business_id=business_id
    )

    if not business:
        raise CustomException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business not found"
        )

    uploaded_files = []

    if not os.path.exists(BUSINESS_UPLOADS):
        os.makedirs(BUSINESS_UPLOADS)

    for file in files:

        content = await file.read()

        file_size = len(content)

        file_type = validate_file(
            file=file,
            file_size=file_size
        )

        extension = file.filename.split(".")[-1]

        unique_file_name = f"{uuid.uuid4()}.{extension}"

        file_path = os.path.join(
            BUSINESS_UPLOADS,
            unique_file_name
        )

        with open(file_path, "wb") as buffer:
            buffer.write(content)

        media_data = {
            "business_id": business_id,
            "file_name": unique_file_name,
            "original_file_name": file.filename,
            "file_type": file_type,
            "mime_type": file.content_type,
            "file_size": file_size,
            "file_url": file_path
        }

        media = create_business_gallery_repo(
            db=db,
            media_data=media_data
        )

        uploaded_files.append(media)

    return uploaded_files

async def upload_listing_media_service(
    db: Session,
    listing_id: UUID,
    files: list[UploadFile]
):

    listing = get_listing_by_id_repo(
        db=db,
        listing_id=listing_id
    )

    if not listing:
        raise CustomException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Listing not found"
        )

    uploaded_files = []

    if not os.path.exists(LISTING_UPLOADS):
        os.makedirs(LISTING_UPLOADS)

    for file in files:

        content = await file.read()

        file_size = len(content)

        file_type = validate_file(
            file=file,
            file_size=file_size
        )

        extension = file.filename.split(".")[-1]

        unique_file_name = f"{uuid.uuid4()}.{extension}"

        file_path = os.path.join(
            LISTING_UPLOADS,
            unique_file_name
        )

        with open(file_path, "wb") as buffer:
            buffer.write(content)

        media_data = {
            "listing_id": listing_id,
            "file_name": unique_file_name,
            "original_file_name": file.filename,
            "file_type": file_type,
            "mime_type": file.content_type,
            "file_size": file_size,
            "file_url": file_path
        }

        media = create_listing_media_repo(
            db=db,
            media_data=media_data
        )

        uploaded_files.append(media)

    return uploaded_files