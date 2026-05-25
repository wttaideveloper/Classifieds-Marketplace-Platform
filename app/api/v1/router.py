from fastapi import APIRouter
import logging

logger = logging.getLogger(__name__)

api_router = APIRouter()


def _safe_include(module_path: str, prefix: str = "", tags: list[str] | None = None):
    try:
        module = __import__(module_path, fromlist=["router"])
        router = getattr(module, "router")
        api_router.include_router(router, prefix=prefix, tags=tags)
    except Exception as e:
        logger.error(f"Failed to load router '{module_path}': {e}")


# CUSTOMER AUTH
_safe_include(
    "app.api.v1.endpoints.customer",
    prefix="/auth/customer",
    tags=["Customer Auth"]
)

# CUSTOMER PROFILE
_safe_include(
    "app.api.v1.endpoints.customer_profile",
    prefix="/customer",
    tags=["Customer Profile"]
)

# ADDRESS
_safe_include(
    "app.api.v1.endpoints.address",
    prefix="/customer/addresses",
    tags=["Address"]
)

# MERCHANT AUTH
_safe_include(
    "app.api.v1.endpoints.merchant",
    prefix="/auth/merchant",
    tags=["Auth Merchant"]
)

# MERCHANT PROFILE
_safe_include(
    "app.api.v1.endpoints.merchant_profile",
    prefix="/merchant",
    tags=["Merchant Profile"]
)

# ADMIN PROFILE
_safe_include(
    "app.api.v1.endpoints.admin_profile",
    prefix="/admin",
    tags=["Admin Profile"]
)

# PUBLIC LISTINGS
_safe_include(
    "app.api.v1.endpoints.public_listing",
    tags=["Public Listings"]
)

# CAPACITY
_safe_include(
    "app.api.v1.endpoints.capacity",
    prefix="/capacity",
    tags=["Capacity"]
)