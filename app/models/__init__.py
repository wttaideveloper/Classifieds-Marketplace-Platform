"""SQLAlchemy models package.

Import all model modules so metadata is registered on ``Base``.
"""

from . import (  # noqa: F401
    address_model,
    admin_model,
    attribute_model,
    customer_model,
    enterprise_model,
    merchant_model,
    product_model,
    service_model,
)

__all__ = [
    "address_model",
    "admin_model",
    "attribute_model",
    "customer_model",
    "enterprise_model",
    "merchant_model",
    "product_model",
    "service_model",
]
