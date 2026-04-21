from app.api.v1.endpoints import customer, customer_profile, auth
from fastapi import APIRouter

api_router = APIRouter()

api_router.include_router(
    auth.router,
    prefix="/auth",
    tags=["Auth"]
)

api_router.include_router(
    customer.router,
    prefix="/auth/customer",
    tags=["Customer Auth"]
)

api_router.include_router(
    customer_profile.router,
    prefix="/customer",
    tags=["Customer Profile"]
)