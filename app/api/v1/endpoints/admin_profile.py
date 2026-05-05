# app/api/v1/endpoints/admin_profile.py
from fastapi import APIRouter, Depends, status, HTTPException, Query, status
from sqlalchemy.orm import Session
from typing import Optional
from uuid import UUID
from app.db.database import SessionLocal
from app.core.dependencies import get_current_user, get_current_admin
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

router = APIRouter(tags=["Admin Profile"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# GET ADMIN PROFILE
@router.get("/profile", status_code=status.HTTP_200_OK)
def get_profile(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):

    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=403,
            detail="Unauthorized"
        )

    return get_admin_profile_service(
        db,
        current_user.get("id")
    )

# UPDATE ADMIN PROFILE
@router.put("/profile", status_code=status.HTTP_200_OK)
def update_profile(
    payload: AdminProfileUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):

    if current_user.get("role") != "admin":
        raise Exception("Unauthorized")

    return update_admin_profile_service(
        db,
        current_user.get("id"),
        payload
    )

@router.get(
    "/users",
    response_model=PaginatedUsers,
    status_code=status.HTTP_200_OK
)
def get_users(
    search: str = Query(None),
    role: str = Query(None),
    status_filter: str = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    
    # Allow only admin
    if current_user["role"] != "admin":
        from app.exceptions.custom_exception import CustomException
        raise CustomException(
            status.HTTP_403_FORBIDDEN,
            "Access denied"
        )

    return admin_get_users_service(
        db=db,
        search=search,
        role=role,
        status=status_filter,
        page=page,
        limit=limit
    )

@router.get(
    "/users/{userId}",
    response_model=UserDetailsResponse,
    status_code=status.HTTP_200_OK
)
def get_user_details(
    userId: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):

    # ADMIN ONLY
    if current_user["role"] != "admin":
        raise CustomException(
            status.HTTP_403_FORBIDDEN,
            "Access denied"
        )

    return admin_get_user_details_service(db, userId)

@router.patch(
    "/users/{userId}/status",
    status_code=status.HTTP_200_OK
)
def update_user_status(
    userId: str,
    payload: UpdateUserStatusSchema,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):

    # ADMIN ONLY
    if current_user["role"] != "admin":
        raise CustomException(
            status.HTTP_403_FORBIDDEN,
            "Access denied"
        )

    return admin_update_user_status_service(
        db,
        userId,
        payload
    )

# MERCHANTS
@router.get(
    "/merchants",
    response_model=MerchantListResponse,
    status_code=status.HTTP_200_OK
)
def get_merchants(
    search: str = Query(None),
    status_filter: str = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):

    # ADMIN ONLY
    if current_user["role"] != "admin":
        raise CustomException(
            status.HTTP_403_FORBIDDEN,
            "Access denied"
        )

    return admin_get_merchants_service(
        db=db,
        search=search,
        status=status_filter,
        page=page,
        limit=limit
    )

@router.get(
    "/merchants/{merchantId}",
    response_model=MerchantDetailsResponse,
    status_code=status.HTTP_200_OK
)
def get_merchant_details(
    merchantId: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):

    # ADMIN ONLY
    if current_user["role"] != "admin":
        raise CustomException(
            status.HTTP_403_FORBIDDEN,
            "Access denied"
        )

    return admin_get_merchant_details_service(
        db,
        merchantId
    )

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
    limit: int = Query(10, le=100),
    db: Session = Depends(get_db)
):
    try:
        result = fetch_businesses_service(
            db=db,
            search=search,
            status=status_filter,
            category=category,
            page=page,
            limit=limit
        )

        return result

    except Exception as e:
        return {
            "error": str(e)
        }
    
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
    # current_admin = Depends(get_current_admin)
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