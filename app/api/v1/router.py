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

# Enterprise Management
_safe_include("app.api.v1.endpoints.enterprise",  prefix="/api/enterprises", tags=["Enterprise"])

# Products
_safe_include("app.api.v1.endpoints.product",     prefix="/api/products", tags=["Products"])

# Services
_safe_include("app.api.v1.endpoints.service", prefix="/api/services", tags=["Services"])

# Dynamic attributes
_safe_include("app.api.v1.endpoints.attribute", prefix="/api/attributes", tags=["Dynamic Attributes"])

# Inventory system
_safe_include("app.api.v1.endpoints.system", prefix="/api", tags=["System"])