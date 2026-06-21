from datetime import date

from app.models.enterprise_model import Enterprise
from app.models.product_model import Product
from app.models.service_model import Service
from app.schemas.common_schema import AvailabilityResponse, EnterpriseStatusLabel


def enterprise_status_label(is_active: bool | None) -> EnterpriseStatusLabel:
    if is_active is True:
        return "active"
    if is_active is False:
        return "inactive"
    return "pending"


def map_enterprise_list_item(enterprise: Enterprise) -> dict:
    base = _enterprise_base_fields(enterprise)
    base.update(
        {
            "category": None,
            "status_label": enterprise_status_label(enterprise.status),
            "members_count": 0,
            "revenue": 0,
            "joined_date": None,
        }
    )
    return base


def map_enterprise_detail(enterprise: Enterprise) -> dict:
    base = _enterprise_base_fields(enterprise)
    base.update(
        {
            "category": None,
            "status_label": enterprise_status_label(enterprise.status),
            "members_count": 0,
            "revenue": 0,
            "rating": 0,
        }
    )
    return base


def map_enterprise_write(enterprise: Enterprise) -> dict:
    return _enterprise_base_fields(enterprise)


def _enterprise_base_fields(enterprise: Enterprise) -> dict:
    return {
        "id": enterprise.id,
        "business_short_name": enterprise.business_short_name,
        "business_legal_name": enterprise.business_legal_name,
        "business_description": enterprise.business_description,
        "business_email": enterprise.business_email,
        "business_phone": enterprise.business_phone,
        "registered_address": enterprise.registered_address,
        "business_address": enterprise.business_address,
        "communication_address": enterprise.communication_address,
        "logo_url": enterprise.logo_url,
        "business_images": enterprise.business_images,
        "status": enterprise.status,
    }


def map_product_list_item(product: Product) -> dict:
    return {
        **_product_base_fields(product),
        "rating": 0,
    }


def map_product_detail(product: Product) -> dict:
    enterprise_name = None
    if product.enterprise is not None:
        enterprise_name = product.enterprise.business_short_name

    return {
        **_product_base_fields(product),
        "enterprise_name": enterprise_name,
        "rating": 0,
        "length": None,
        "width": None,
        "thick": None,
        "stock_count": 0,
    }


def map_product_write(product: Product) -> dict:
    return _product_base_fields(product)


def _product_base_fields(product: Product) -> dict:
    return {
        "id": product.id,
        "enterprise_id": product.enterprise_id,
        "product_name": product.product_name,
        "product_description": product.product_description,
        "product_category": product.product_category,
        "product_price": product.product_price,
        "product_images": product.product_images,
        "product_status": product.product_status,
    }


def map_service_list_item(service: Service) -> dict:
    return {
        **_service_base_fields(service),
        "trainer_name": None,
    }


def map_service_detail(service: Service) -> dict:
    return {
        **_service_base_fields(service),
        "banner_image": None,
        "trainer_name": None,
        "expertise_name": None,
        "type": None,
        "format": None,
        "availability": AvailabilityResponse().model_dump(),
    }


def map_service_write(service: Service) -> dict:
    return _service_base_fields(service)


def _service_base_fields(service: Service) -> dict:
    return {
        "id": service.id,
        "enterprise_id": service.enterprise_id,
        "service_name": service.service_name,
        "service_description": service.service_description,
        "service_category": service.service_category,
        "service_price": service.service_price,
        "duration": service.duration,
        "availability_status": service.availability_status,
        "service_status": service.service_status,
    }
