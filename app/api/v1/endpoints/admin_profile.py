# app/api/v1/endpoints/admin_profile.py
from fastapi import APIRouter, Depends, status, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
from uuid import UUID

from app.db.database import SessionLocal
from app.schemas.admin_schema import (
    AdminProfileUpdate,
    UserDetailsResponse,
    PaginatedUsers,
    UpdateUserStatusSchema,
    MerchantListResponse,
    MerchantDetailsResponse,
    BusinessListResponse,
    BusinessDetailResponse,
    BusinessApproveResponse,
    BusinessRejectResponse,
    BusinessRejectRequest,
    BusinessSuspendResponse,
    BusinessSuspendRequest,
    BusinessReactivateResponse,
    MerchantResponse,
    RejectListingRequest,
    SuspendListingRequest,
    SuspendListingResponse,
    ReactivateListingResponse,
    CreateCategoryRequest,
    CreateCategoryResponse,
    AttributeCreate,
    AttributeUpdate,
    AttributeResponse
)
from app.services.admin_service import (
    get_admin_profile_service,
    update_admin_profile_service,
    admin_get_users_service,
    admin_get_user_details_service,
    admin_update_user_status_service,
    admin_get_merchants_service,
    admin_get_merchant_details_service,
    fetch_businesses_service,
    fetch_business_detail_service,
    approve_business_service,
    reject_business_service,
    suspend_business_service,
    reactivate_business_service,
    get_associated_merchant_service,
    get_all_listings_service,
    approve_listing_service,
    reject_listing_service,
    suspend_listing_service,
    reactivate_listing_service,
    create_category_service,
    create_attribute_service,
    get_all_attributes_service,
    get_attribute_by_id_service,
    update_attribute_service,
    delete_attribute_service
)
from app.exceptions.custom_exception import CustomException
from app.db.database import get_db

router = APIRouter(
    tags=["Admin"]
)

#  ADMIN PROFILE 
@router.get("/profile", status_code=status.HTTP_200_OK)
def get_profile(
    admin_id: str = Query(..., description="Admin id"),
    db: Session = Depends(get_db),
):
    return get_admin_profile_service(db, admin_id)

@router.put("/profile", status_code=status.HTTP_200_OK)
def update_profile(
    payload: AdminProfileUpdate,
    admin_id: str = Query(..., description="Admin id"),
    db: Session = Depends(get_db)
):
    return update_admin_profile_service(
        db,
        admin_id,
        payload
    )

#  USERS
@router.get(
    "/users",
    response_model=PaginatedUsers,
    status_code=status.HTTP_200_OK
)
def get_users(
    search: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return admin_get_users_service(
        db=db,
        search=search,
        role=role,
        status=status_filter,
        page=page,
        limit=limit
    )

@router.get(
    "/users/{user_id}",
    response_model=UserDetailsResponse,
    status_code=status.HTTP_200_OK
)
def get_user_details(
    user_id: str,
    db: Session = Depends(get_db)
):
    return admin_get_user_details_service(db, user_id)


@router.patch(
    "/users/{user_id}/status",
    status_code=status.HTTP_200_OK
)
def update_user_status(
    user_id: str,
    payload: UpdateUserStatusSchema,
    db: Session = Depends(get_db)
):
    return admin_update_user_status_service(
        db,
        user_id,
        payload
    )

#  MERCHANTS 
@router.get(
    "/merchants",
    response_model=MerchantListResponse,
    status_code=status.HTTP_200_OK
)
def get_merchants(
    search: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return admin_get_merchants_service(
        db=db,
        search=search,
        status=status_filter,
        page=page,
        limit=limit
    )

@router.get(
    "/merchants/{merchant_id}",
    response_model=MerchantDetailsResponse,
    status_code=status.HTTP_200_OK
)
def get_merchant_details(
    merchant_id: UUID,
    db: Session = Depends(get_db)
):
    return admin_get_merchant_details_service(
        db,
        merchant_id
    )

# BUSINESSES
@router.get(
    "/businesses",
    response_model=BusinessListResponse,
    status_code=status.HTTP_200_OK
)
def get_all_businesses(
    search: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    category: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db)
):
    return fetch_businesses_service(
        db=db,
        search=search,
        status=status_filter,
        category=category,
        page=page,
        limit=limit
    )


@router.get(
    "/businesses/{business_id}",
    response_model=BusinessDetailResponse,
    status_code=status.HTTP_200_OK
)
def get_business_detail(
    business_id: UUID,
    db: Session = Depends(get_db)
):
    return fetch_business_detail_service(db, business_id)


@router.patch(
    "/businesses/{business_id}/approve",
    response_model=BusinessApproveResponse,
    status_code=status.HTTP_200_OK
)
def approve_business(
    business_id: UUID,
    db: Session = Depends(get_db)
):
    return approve_business_service(db, business_id)


@router.patch(
    "/businesses/{business_id}/reject",
    response_model=BusinessRejectResponse,
    status_code=status.HTTP_200_OK
)
def reject_business(
    business_id: UUID,
    payload: BusinessRejectRequest,
    db: Session = Depends(get_db)
):
    return reject_business_service(
        db=db,
        business_id=business_id,
        reason=payload.reason
    )


@router.patch(
    "/businesses/{business_id}/suspend",
    response_model=BusinessSuspendResponse,
    status_code=status.HTTP_200_OK
)
def suspend_business(
    business_id: UUID,
    payload: BusinessSuspendRequest,
    db: Session = Depends(get_db)
):
    return suspend_business_service(
        db=db,
        business_id=business_id,
        reason=payload.reason
    )


@router.patch(
    "/businesses/{business_id}/reactivate",
    response_model=BusinessReactivateResponse,
    status_code=status.HTTP_200_OK
)
def reactivate_business(
    business_id: UUID,
    db: Session = Depends(get_db)
):
    return reactivate_business_service(db, business_id)


@router.get(
    "/businesses/{business_id}/merchant",
    response_model=MerchantResponse,
    status_code=status.HTTP_200_OK
)
def get_associated_merchant(
    business_id: UUID,
    db: Session = Depends(get_db)
):
    return get_associated_merchant_service(db, business_id)

# GET ALL LISTINGS
@router.get(
    "/listings",
    status_code=status.HTTP_200_OK
)
def get_all_listings(
    db: Session = Depends(get_db)
):

    return get_all_listings_service(
        db=db
    )

# APPROVE LISTING
@router.patch(
    "/listings/{listing_id}/approve",
    status_code=status.HTTP_200_OK
)
def approve_listing(
    listing_id: UUID,
    db: Session = Depends(get_db)
):
    return approve_listing_service(
        db=db,
        listing_id=listing_id
    )

# REJECT LISTING
@router.patch(
    "/listings/{listing_id}/reject",
    status_code=status.HTTP_200_OK
)
def reject_listing(
    listing_id: UUID,
    payload: RejectListingRequest,
    db: Session = Depends(get_db)
):

    return reject_listing_service(
        db=db,
        listing_id=listing_id,
        payload=payload
    )

# SUSPEND LISTING
@router.patch(
    "/listings/{listing_id}/suspend",
    response_model=SuspendListingResponse,
    status_code=status.HTTP_200_OK
)
def suspend_listing(
    listing_id: UUID,
    payload: SuspendListingRequest,
    db: Session = Depends(get_db)
):

    return suspend_listing_service(
        db=db,
        listing_id=listing_id,
        payload=payload
    )

# REACTIVATE LISTING
@router.patch(
    "/listings/{listing_id}/reactivate",
    response_model=ReactivateListingResponse,
    status_code=status.HTTP_200_OK
)
def reactivate_listing(
    listing_id: UUID,
    db: Session = Depends(get_db)
):

    return reactivate_listing_service(
        db=db,
        listing_id=listing_id
    )

# CREATE CATEGORY
@router.post(
    "/categories",
    response_model=CreateCategoryResponse,
    status_code=status.HTTP_201_CREATED
)
def create_category(
    payload: CreateCategoryRequest,
    db: Session = Depends(get_db)
):

    return create_category_service(
        db=db,
        payload=payload
    )

# CREATE ATTRIBUTE
@router.post(
    "/attributes",
    response_model=AttributeResponse,
    status_code=status.HTTP_201_CREATED
)
def create_attribute(
    payload: AttributeCreate,
    db: Session = Depends(get_db)
):

    return create_attribute_service(
        db,
        payload
    )


# GET ALL ATTRIBUTES
@router.get(
    "/attributes",
    response_model=list[AttributeResponse],
    status_code=status.HTTP_200_OK
)
def get_all_attributes(
    db: Session = Depends(get_db)
):

    return get_all_attributes_service(db)


# GET ATTRIBUTE BY ID
@router.get(
    "/attributes/{attribute_id}",
    response_model=AttributeResponse,
    status_code=status.HTTP_200_OK
)
def get_attribute_by_id(
    attribute_id: UUID,
    db: Session = Depends(get_db)
):

    return get_attribute_by_id_service(
        db,
        attribute_id
    )


# UPDATE ATTRIBUTE
@router.put(
    "/attributes/{attribute_id}",
    response_model=AttributeResponse,
    status_code=status.HTTP_200_OK
)
def update_attribute(
    attribute_id: UUID,
    payload: AttributeUpdate,
    db: Session = Depends(get_db)
):

    return update_attribute_service(
        db,
        attribute_id,
        payload
    )


# DELETE ATTRIBUTE
@router.delete(
    "/attributes/{attribute_id}",
    status_code=status.HTTP_200_OK
)
def delete_attribute(
    attribute_id: UUID,
    db: Session = Depends(get_db)
):

    return delete_attribute_service(
        db,
        attribute_id
    )

