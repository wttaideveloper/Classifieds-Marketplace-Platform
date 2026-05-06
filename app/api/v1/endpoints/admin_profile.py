# app/api/v1/endpoints/admin_profile.py

from fastapi import APIRouter, Depends, status, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
from uuid import UUID

from app.db.database import SessionLocal
from app.core.dependencies import get_current_admin
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
    MerchantResponse
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
    get_associated_merchant_service
)
from app.exceptions.custom_exception import CustomException


router = APIRouter(
    tags=["Admin"]
)


# DB Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


#  ADMIN PROFILE 
@router.get("/profile", status_code=status.HTTP_200_OK)
def get_profile(
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin)
):
    return get_admin_profile_service(db, current_admin["id"])


@router.put("/profile", status_code=status.HTTP_200_OK)
def update_profile(
    payload: AdminProfileUpdate,
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin)
):
    return update_admin_profile_service(
        db,
        current_admin["id"],
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
    current_admin=Depends(get_current_admin)
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
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin)
):
    return admin_get_user_details_service(db, user_id)


@router.patch(
    "/users/{user_id}/status",
    status_code=status.HTTP_200_OK
)
def update_user_status(
    user_id: str,
    payload: UpdateUserStatusSchema,
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin)
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
    current_admin=Depends(get_current_admin)
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
    merchant_id: str,
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin)
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
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin)
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
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin)
):
    return fetch_business_detail_service(db, business_id)


@router.patch(
    "/businesses/{business_id}/approve",
    response_model=BusinessApproveResponse,
    status_code=status.HTTP_200_OK
)
def approve_business(
    business_id: UUID,
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin)
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
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin)
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
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin)
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
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin)
):
    return reactivate_business_service(db, business_id)


@router.get(
    "/businesses/{business_id}/merchant",
    response_model=MerchantResponse,
    status_code=status.HTTP_200_OK
)
def get_associated_merchant(
    business_id: UUID,
    db: Session = Depends(get_db),
    current_admin=Depends(get_current_admin)
):
    return get_associated_merchant_service(db, business_id)