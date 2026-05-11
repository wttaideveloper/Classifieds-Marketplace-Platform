from fastapi import APIRouter
from app.api.v1.endpoints import ( 
    customer_profile, 
    auth, 
    address, 
    customer, 
    merchant, 
    merchant_profile, 
    admin, 
    admin_profile,
    public_listing
)

api_router = APIRouter()

print("AUTH ROUTES LOADED")
# AUTH
api_router.include_router(
    auth.router,
    prefix="/auth",
    tags=["Auth"]
)

# Customer login
api_router.include_router(
    customer.router,
    prefix="/auth/customer",
    tags=["Auth Customer"]
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
    tags=["Merchant Auth"]
)

# MERCHANT PROFILE
api_router.include_router(
    merchant_profile.router,
    prefix="/merchant",
    tags=["Merchant Profile"]
)

# ADMIN AUTH
api_router.include_router(
    admin.router,
    prefix="/auth/admin",
    tags=["Admin Auth"]
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