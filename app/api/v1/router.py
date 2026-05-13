from fastapi import APIRouter
from app.api.v1.endpoints import ( 
    customer_profile, 
    address, 
    merchant_profile, 
    admin_profile,
    public_listing
)

api_router = APIRouter()

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