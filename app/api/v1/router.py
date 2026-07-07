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

# Onboarding form templates
_safe_include(
    "app.api.v1.endpoints.onboarding_form",
    prefix="/onboarding-forms",
    tags=["Onboarding Forms"],
)

# Chat system
_safe_include("app.api.v1.endpoints.conversation", prefix="/conversations", tags=["Conversations"])
_safe_include(
    "app.api.v1.endpoints.conversation_messages",
    prefix="/conversations",
    tags=["Conversations"],
)
_safe_include(
    "app.api.v1.endpoints.typing_indicator",
    prefix="/conversations",
    tags=["Typing Indicator"],
)
_safe_include("app.api.v1.endpoints.message", prefix="/messages", tags=["Messages"])
_safe_include("app.api.v1.endpoints.attachment", prefix="/attachments", tags=["Attachments"])
_safe_include(
    "app.api.v1.endpoints.chat_notification",
    prefix="/notifications",
    tags=["Chat Notifications"],
)
_safe_include("app.api.v1.endpoints.device", prefix="/devices", tags=["Devices"])
_safe_include(
    "app.api.v1.endpoints.provider_assignment",
    prefix="/providers",
    tags=["Provider Assignment"],
)
_safe_include(
    "app.api.v1.endpoints.chat_subscription",
    prefix="/subscriptions",
    tags=["Chat Subscriptions"],
)
_safe_include("app.api.v1.endpoints.presence", prefix="/presence", tags=["Presence"])
_safe_include("app.api.v1.endpoints.socket_io", prefix="/socket-io", tags=["Socket.IO"])
_safe_include("app.api.v1.endpoints.chat_admin", prefix="/admin/chat", tags=["Chat Administration"])

# System
_safe_include("app.api.v1.endpoints.auth", prefix="/auth", tags=["Authentication"])

# System
