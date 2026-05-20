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


# AUTH (shared: forgot/reset/change password, logout, refresh, me, verify-email)
_safe_include("app.api.v1.endpoints.auth", tags=["Auth"])

# CUSTOMER
_safe_include("app.api.v1.endpoints.customer", prefix="/auth/customer", tags=["Customer Auth"])
_safe_include("app.api.v1.endpoints.customer_profile", prefix="/customer", tags=["Customer Profile"])
_safe_include("app.api.v1.endpoints.address", prefix="/customer/addresses", tags=["Address"])
_safe_include("app.api.v1.endpoints.users", prefix="/users", tags=["Users"])

# MERCHANT
_safe_include("app.api.v1.endpoints.merchant", prefix="/auth/merchant", tags=["Merchant Auth"])
_safe_include("app.api.v1.endpoints.merchant_profile", prefix="/merchant", tags=["Merchant Profile"])

# ADMIN
_safe_include("app.api.v1.endpoints.admin", prefix="/auth/admin", tags=["Admin Auth"])
_safe_include("app.api.v1.endpoints.admin_profile", prefix="/admin", tags=["Admin Profile"])

# PUBLIC LISTINGS
_safe_include("app.api.v1.endpoints.public_listing", tags=["Public Listings"])

# SEARCH & DISCOVERY
_safe_include("app.api.v1.endpoints.search", tags=["Search & Discovery"])

# REVIEWS & RATINGS
_safe_include("app.api.v1.endpoints.review", tags=["Reviews & Ratings"])

# MERCHANT BLOGS
_safe_include("app.api.v1.endpoints.blog_merchant", prefix="/merchant", tags=["Merchant Blogs"])

# ADMIN BLOGS
_safe_include("app.api.v1.endpoints.blog_admin", prefix="/admin", tags=["Admin Blogs"])

# PUBLIC BLOGS + CATEGORIES
_safe_include("app.api.v1.endpoints.blog_public", tags=["Public Blogs"])
_safe_include("app.api.v1.endpoints.blog_categories", tags=["Blog Categories"])