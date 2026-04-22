from fastapi import APIRouter
from app.api.v1.endpoints import customer_profile, auth, address, customer

api_router = APIRouter()

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