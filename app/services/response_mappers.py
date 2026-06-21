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


def _joined_date(created_at) -> date | None:
    if created_at is None:
        return None
    return created_at.date() if hasattr(created_at, "date") else None


def schedule_to_availability(schedule: list | None) -> dict:
    if not schedule:
        return AvailabilityResponse().model_dump()

    day_wise_slot_count: dict[str, int] = {}
    slot_timings: list[str] = []

    for entry in schedule:
        if not isinstance(entry, dict):
            continue
        if not entry.get("is_available", True):
            continue
        day = entry.get("day")
        if not day:
            continue
        day_wise_slot_count[day] = day_wise_slot_count.get(day, 0) + 1
        start = entry.get("start_time", "")
        end = entry.get("end_time", "")
        slot_timings.append(f"{start}-{end}")

    return AvailabilityResponse(
        week_dates=sorted(day_wise_slot_count.keys()),
        day_wise_slot_count=day_wise_slot_count,
        slot_timings=slot_timings,
    ).model_dump()


def map_enterprise_list_item(enterprise: Enterprise) -> dict:
    base = _enterprise_base_fields(enterprise)
    base.update(
        {
            "category": enterprise.business_category,
            "status_label": enterprise_status_label(enterprise.status),
            "members_count": 0,
            "revenue": 0,
            "joined_date": _joined_date(enterprise.created_at),
        }
    )
    return base


def map_enterprise_detail(enterprise: Enterprise) -> dict:
    base = _enterprise_base_fields(enterprise)
    base.update(
        {
            "category": enterprise.business_category,
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
        "suite_unit": enterprise.suite_unit,
        "logo_url": enterprise.logo_url,
        "business_images": enterprise.business_images,
        "registration_number": enterprise.registration_number,
        "business_category": enterprise.business_category,
        "website_url": enterprise.website_url,
        "year_founded": enterprise.year_founded,
        "primary_contact_name": enterprise.primary_contact_name,
        "primary_contact_title": enterprise.primary_contact_title,
        "secondary_email": enterprise.secondary_email,
        "secondary_phone": enterprise.secondary_phone,
        "brand_color": enterprise.brand_color,
        "tagline": enterprise.tagline,
        "status": enterprise.status,
        "created_at": enterprise.created_at,
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
        "stock_count": product.stock_quantity,
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
        "sku": product.sku,
        "barcode_upc": product.barcode_upc,
        "weight": product.weight,
        "dimensions": product.dimensions,
        "sale_price": product.sale_price,
        "cost_price": product.cost_price,
        "tax_class": product.tax_class,
        "currency": product.currency,
        "stock_quantity": product.stock_quantity,
        "low_stock_alert_threshold": product.low_stock_alert_threshold,
        "stock_management": product.stock_management,
        "publish_status": product.publish_status,
        "created_at": product.created_at,
    }


def map_service_list_item(service: Service) -> dict:
    base = _service_base_fields(service)
    base["trainer_name"] = service.instructor_name
    return base


def map_service_detail(service: Service) -> dict:
    base = _service_base_fields(service)
    base.update(
        {
            "trainer_name": service.instructor_name,
            "format": service.delivery_format,
            "availability": schedule_to_availability(service.availability_schedule),
        }
    )
    return base


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
        "max_participants": service.max_participants,
        "provider_name": service.provider_name,
        "instructor_name": service.instructor_name,
        "delivery_format": service.delivery_format,
        "package_price": service.package_price,
        "currency": service.currency,
        "cancellation_policy": service.cancellation_policy,
        "availability_schedule": service.availability_schedule,
        "created_at": service.created_at,
    }
