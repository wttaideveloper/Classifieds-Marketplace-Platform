from fastapi import APIRouter, status, Depends
from app.schemas.customer_schema import UpdateCustomerProfile
from app.services.customer_service import *
from app.core.dependencies import get_current_user, get_current_admin

router = APIRouter()

@router.get("/profile")
def get_profile(current_user: dict = Depends(get_current_user)):
    return get_customer_profile_service(current_user)

@router.put("/profile")
def update_profile(payload: UpdateCustomerProfile,
                   current_user: dict = Depends(get_current_user)):
    return update_customer_profile_service(current_user, payload)
