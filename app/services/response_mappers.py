from datetime import date, timedelta

from app.models.enterprise_model import Enterprise
from app.models.location_model import EnterpriseLocation
from app.models.product_model import Product
from app.models.service_model import Service
from app.schemas.common_schema import EnterpriseStatusLabel

_WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_DAY_LABELS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


def enterprise_status_label(status_value: str | bool | None) -> EnterpriseStatusLabel:
    if isinstance(status_value, bool):
        return "active" if status_value else "inactive"
    if status_value == "active":
        return "active"
    if status_value == "inactive":
        return "inactive"
    if status_value == "draft":
        return "draft"
    return "pending"


def _joined_date(created_at) -> date | None:
    if created_at is None:
        return None
    return created_at.date() if hasattr(created_at, "date") else None


def _parse_minutes(time_value: str) -> int:
    hours, minutes = time_value.strip().split(":", 1)
    return int(hours) * 60 + int(minutes)


def _format_minutes(total_minutes: int) -> str:
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02d}:{minutes:02d}"


def _generate_slots(start_time: str, end_time: str, slot_length: str) -> list[str]:
    slot_minutes = int(slot_length)
    if slot_minutes <= 0:
        return []

    start = _parse_minutes(start_time)
    end = _parse_minutes(end_time)
    slots: list[str] = []

    current = start
    while current + slot_minutes <= end:
        slots.append(
            f"{_format_minutes(current)}-{_format_minutes(current + slot_minutes)}"
        )
        current += slot_minutes

    return slots


def _date_for_weekday(day_name: str, reference: date | None = None) -> date:
    reference = reference or date.today()
    weekday = _WEEKDAY_INDEX.get(day_name.strip().lower())
    if weekday is None:
        return reference
    days_ahead = (weekday - reference.weekday()) % 7
    return reference + timedelta(days=days_ahead)


def _day_label(day_name: str) -> str:
    weekday = _WEEKDAY_INDEX.get(day_name.strip().lower())
    if weekday is None:
        return day_name.strip().title()
    return _DAY_LABELS[weekday]


def schedule_to_availability_days(
    schedule: list | None,
    reference: date | None = None,
) -> list[dict]:
    if not schedule:
        return []

    reference = reference or date.today()
    availability: list[dict] = []

    for entry in schedule:
        if not isinstance(entry, dict):
            continue
        if not entry.get("is_available", True):
            continue

        day_name = entry.get("day")
        start_time = entry.get("start_time")
        end_time = entry.get("end_time")
        slot_length = entry.get("slot_length")
        if not day_name or not start_time or not end_time or not slot_length:
            continue

        slots = _generate_slots(start_time, end_time, str(slot_length))
        if not slots:
            continue

        day_date = _date_for_weekday(day_name, reference)
        availability.append(
            {
                "day": _day_label(day_name),
                "date": day_date.isoformat(),
                "slots": slots,
            }
        )

    return availability


def _service_type_value(service: Service) -> str | None:
    return service.service_type or service.service_category


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
    website = enterprise.website or enterprise.website_url
    banner_url = enterprise.banner_url or enterprise.business_images
    return {
        "id": enterprise.id,
        "tenant_id": enterprise.tenant_id,
        "business_short_name": enterprise.business_short_name,
        "business_legal_name": enterprise.business_legal_name,
        "business_description": enterprise.business_description,
        "business_email": enterprise.business_email,
        "business_phone": enterprise.business_phone,
        "registered_address": enterprise.registered_address,
        "business_address": enterprise.business_address,
        "communication_address": enterprise.communication_address,
        "website": website,
        "logo_url": enterprise.logo_url,
        "banner_url": banner_url,
        "status": enterprise.status,
        "suite_unit": enterprise.suite_unit,
        "business_images": enterprise.business_images,
        "registration_number": enterprise.registration_number,
        "business_category": enterprise.business_category,
        "website_url": enterprise.website_url or website,
        "year_founded": enterprise.year_founded,
        "primary_contact_name": enterprise.primary_contact_name,
        "primary_contact_title": enterprise.primary_contact_title,
        "secondary_email": enterprise.secondary_email,
        "secondary_phone": enterprise.secondary_phone,
        "brand_color": enterprise.brand_color,
        "tagline": enterprise.tagline,
        "created_at": enterprise.created_at,
    }


def map_location(location: EnterpriseLocation) -> dict:
    return {
        "id": location.id,
        "enterprise_id": location.enterprise_id,
        "location_name": location.location_name,
        "address_line_1": location.address_line_1,
        "address_line_2": location.address_line_2,
        "city": location.city,
        "state": location.state,
        "country": location.country,
        "postal_code": location.postal_code,
        "phone": location.phone,
        "email": location.email,
        "latitude": location.latitude,
        "longitude": location.longitude,
        "status": location.status,
        "created_at": location.created_at,
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
        "tenant_id": product.tenant_id,
        "enterprise_id": product.enterprise_id,
        "location_id": product.location_id,
        "product_name": product.product_name,
        "description": product.product_description,
        "category": product.product_category,
        "price": product.product_price,
        "currency": product.currency,
        "image_urls": product.product_images,
        "status": product.status,
        "product_description": product.product_description,
        "product_category": product.product_category,
        "product_price": product.product_price,
        "product_images": product.product_images,
        "product_status": product.product_status,
        "sku": product.sku,
        "barcode_upc": product.barcode_upc,
        "weight": product.weight,
        "dimensions": product.dimensions,
        "length": product.length,
        "width": product.width,
        "thick": product.thick,
        "sale_price": product.sale_price,
        "cost_price": product.cost_price,
        "tax_class": product.tax_class,
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
    enterprise_name = None
    if service.enterprise is not None:
        enterprise_name = service.enterprise.business_short_name

    base = _service_base_fields(service)
    base.update(
        {
            "enterprise_name": enterprise_name,
            "type": _service_type_value(service),
            "trainer_name": service.instructor_name,
            "format": service.delivery_format,
            "availability": schedule_to_availability_days(service.availability_schedule),
        }
    )
    return base


def map_service_write(service: Service) -> dict:
    return _service_base_fields(service)


def _service_base_fields(service: Service) -> dict:
    return {
        "id": service.id,
        "tenant_id": service.tenant_id,
        "enterprise_id": service.enterprise_id,
        "location_id": service.location_id,
        "service_name": service.service_name,
        "description": service.service_description,
        "category": service.service_category,
        "duration_minutes": service.duration,
        "price": service.service_price,
        "currency": service.currency,
        "availability": service.availability_schedule,
        "status": service.status,
        "service_description": service.service_description,
        "service_category": service.service_category,
        "service_price": service.service_price,
        "duration": service.duration,
        "service_type": service.service_type,
        "banner_image": service.banner_image,
        "availability_status": service.availability_status,
        "service_status": service.service_status,
        "max_participants": service.max_participants,
        "provider_name": service.provider_name,
        "provider_user_id": service.provider_user_id,
        "instructor_name": service.instructor_name,
        "delivery_format": service.delivery_format,
        "package_price": service.package_price,
        "cancellation_policy": service.cancellation_policy,
        "availability_schedule": service.availability_schedule,
        "created_at": service.created_at,
    }
