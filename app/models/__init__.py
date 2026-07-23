"""SQLAlchemy models package.

Import all model modules so metadata is registered on ``Base``.
"""

from . import (  # noqa: F401
    attribute_model,
    chat_model,
    enterprise_model,
    location_model,
    notification_model,
    onboarding_form_model,
    product_model,
    service_model,
)

__all__ = [
    "attribute_model",
    "chat_model",
    "enterprise_model",
    "location_model",
    "notification_model",
    "onboarding_form_model",
    "product_model",
    "service_model",
]
