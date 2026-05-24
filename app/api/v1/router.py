from fastapi import APIRouter
from app.api.v1.endpoints import ( 
    customer,
    customer_profile, 
    address, 
    merchant,
    merchant_profile, 
    admin_profile,
    public_listing,
    capacity
)

api_router = APIRouter()

# CUSTOMER AUTH
api_router.include_router(
    customer.router,
    prefix="/auth/customer",
    tags=["Customer Auth"]
)

# CUSTOMER PROFILE
api_router.include_router(
    customer_profile.router,
    prefix="/customer",
    tags=["Customer Profile"]
)

# ADDRESS
api_router.include_router(
    address.router,
    prefix="/customer/addresses",
    tags=["Address"]
)

# MERCHANT AUTH
api_router.include_router(
    merchant.router,
    prefix="/auth/merchant",
    tags=["Auth Merchant"]
)

# MERCHANT PROFILE
api_router.include_router(
    merchant_profile.router,
    prefix="/merchant",
    tags=["Merchant Profile"]
)

# ADMIN PROFILE
api_router.include_router(
    admin_profile.router,
    prefix="/admin",
    tags=["Admin Profile"]
)

# PUBLIC LISTINGS
api_router.include_router(
    public_listing.router,
    tags=["Public Listings"]
)

# Capacity 
api_router.include_router(
    capacity.router,
    prefix="/capacity",
    tags=["Capacity"]
)