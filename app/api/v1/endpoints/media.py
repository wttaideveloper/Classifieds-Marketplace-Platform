from fastapi import (
    APIRouter,
    UploadFile,
    File,
    Depends,
    status
)
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.media_schema import UploadResponse, GetMediaResponse, DeleteMediaResponse, BusinessGalleryUploadResponse, ListingMediaUploadResponse
from app.services.media_service import upload_media_service, get_media_by_id_service, delete_media_service, upload_business_gallery_service, upload_listing_media_service
from uuid import UUID

router = APIRouter(
    tags=["Media"]
)


@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED
)
async def upload_media(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db)
):

    uploaded_files = await upload_media_service(
        db=db,
        files=files
    )

    return {
        "success": True,
        "message": "Files uploaded successfully",
        "data": uploaded_files
    }

@router.get(
    "/{media_id}",
    response_model=GetMediaResponse,
    status_code=status.HTTP_200_OK
)
async def get_media_by_id(
    media_id: UUID,
    db: Session = Depends(get_db)
):

    media = get_media_by_id_service(
        db=db,
        media_id=id
    )

    return {
        "success": True,
        "message": "Media fetched successfully",
        "data": media
    }

@router.delete(
    "/{media_id}",
    response_model=DeleteMediaResponse,
    status_code=status.HTTP_200_OK
)
async def delete_media(
    media_id: UUID,
    db: Session = Depends(get_db)
):

    delete_media_service(
        db=db,
        media_id=id
    )

    return {
        "success": True,
        "message": "Media deleted successfully"
    }

@router.post(
    "/business/{business_id}/gallery",
    response_model=BusinessGalleryUploadResponse,
    status_code=status.HTTP_201_CREATED
)
async def upload_business_gallery(
    business_id: UUID,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db)
):

    uploaded_files = await upload_business_gallery_service(
        db=db,
        business_id=business_id,
        files=files
    )

    return {
        "success": True,
        "message": "Business gallery uploaded successfully",
        "data": uploaded_files
    }

@router.post(
    "/listing/{listing_id}/media",
    response_model=ListingMediaUploadResponse,
    status_code=status.HTTP_201_CREATED
)
async def upload_listing_media(
    listing_id: UUID,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db)
):

    uploaded_files = await upload_listing_media_service(
        db=db,
        listing_id=listing_id,
        files=files
    )

    return {
        "success": True,
        "message": "Listing media uploaded successfully",
        "data": uploaded_files
    }