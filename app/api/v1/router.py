from fastapi import APIRouter
import logging

logger = logging.getLogger(__name__)
api_router = APIRouter()


def _safe_include(module_path: str, prefix: str = "", tags: list[str] | None = None):
    module = __import__(module_path, fromlist=["router"])
    router = getattr(module, "router")
    api_router.include_router(router, prefix=prefix, tags=tags)


# Enterprise Management
_safe_include("app.api.v1.endpoints.enterprise", prefix="/enterprises", tags=["Enterprise"])
_safe_include(
    "app.api.v1.endpoints.enterprise_locations",
    prefix="/enterprises",
    tags=["Enterprise Locations"],
)

# Enterprise Locations (direct access)
_safe_include("app.api.v1.endpoints.location", prefix="/locations", tags=["Enterprise Locations"])

# Products
_safe_include("app.api.v1.endpoints.product", prefix="/products", tags=["Products"])

# Services
_safe_include("app.api.v1.endpoints.service", prefix="/services", tags=["Services"])

# Dynamic attributes
_safe_include(
    "app.api.v1.endpoints.attribute",
    prefix="/dynamic-attributes",
    tags=["Dynamic Attributes"],
)
# Backward-compatible alias
_safe_include(
    "app.api.v1.endpoints.attribute",
    prefix="/attributes",
    tags=["Dynamic Attributes (Legacy)"],
)

# Search APIs
_safe_include("app.api.v1.endpoints.search", prefix="/search", tags=["Search"])

# System
_safe_include("app.api.v1.endpoints.system", prefix="", tags=["System"])
