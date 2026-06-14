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

# ADMIN
_safe_include("app.api.v1.endpoints.admin", prefix="/auth/admin", tags=["Admin Auth"])
_safe_include("app.api.v1.endpoints.admin_profile", prefix="/admin", tags=["Admin Profile"])
_safe_include("app.api.v1.endpoints.admin_moderation", prefix="/admin", tags=["Admin Moderation"])

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

# ORDERS
_safe_include("app.api.v1.endpoints.orders", prefix="/orders", tags=["Orders"])

# Capacity 
_safe_include("app.api.v1.endpoints.capacity", prefix="/capacity", tags=["Capacity"])

# NOTIFICATIONS
_safe_include("app.api.v1.endpoints.notifications", prefix="/notifications", tags=["Notifications"])

# Wishlist
_safe_include("app.api.v1.endpoints.wishlist",  prefix="/wishlist", tags=["Wishlist"])

# Media Uploads
_safe_include("app.api.v1.endpoints.media",  prefix="/media", tags=["Media"])

# Staff
_safe_include("app.api.v1.endpoints.staff",  prefix="/staff", tags=["Staff"])

# Audit Logs
_safe_include("app.api.v1.endpoints.audit_logs",  prefix="/audit", tags=["Audit"])

# Calendar Synchronization
_safe_include("app.api.v1.endpoints.calendar",  prefix="/calendar", tags=["Calendar"])

# Video Meeting Integration
_safe_include("app.api.v1.endpoints.meeting",  prefix="/meetings", tags=["Meetings"])

# Community Ecosystem
_safe_include("app.api.v1.endpoints.community_ecosystem",  prefix="/community-ecosystem", tags=["Community Ecosystem"])

# Enterprise Setup
_safe_include("app.api.v1.endpoints.enterprise_setup",  prefix="/enterprise", tags=["Enterprise Setup"])

# Scheduling
_safe_include("app.api.v1.endpoints.scheduling",  prefix="/scheduling", tags=["Scheduling"])