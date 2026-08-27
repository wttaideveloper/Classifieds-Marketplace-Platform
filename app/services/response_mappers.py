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


def map_event_list_item(event) -> dict:
    return _event_base_fields(event)


def map_event_detail(event) -> dict:
    enterprise_name = None
    if getattr(event, "enterprise", None) is not None:
        enterprise_name = event.enterprise.business_short_name
    base = _event_base_fields(event)
    base["enterprise_name"] = enterprise_name
    return base


def _event_available_seats(event) -> int | None:
    try:
        if event.capacity is None:
            return None
        cap = int(str(event.capacity))
        # count confirmed/attended if relationship loaded
        regs = getattr(event, "registrations", None)
        if regs is not None:
            try:
                cnt = sum(1 for r in regs if getattr(r, "status", None) in ("confirmed", "attended"))
            except Exception:
                cnt = len(regs)
            return max(0, cap - cnt)
        return None
    except Exception:
        return None

def _event_is_full(event) -> bool | None:
    av = _event_available_seats(event)
    if av is None:
        return None
    return av <= 0

def _event_registration_open(event) -> bool | None:
    try:
        from datetime import datetime
        now = datetime.utcnow()
        if event.registration_open_at and now < event.registration_open_at:
            return False
        if event.registration_close_at and now > event.registration_close_at:
            return False
        if event.registration_cutoff and now > event.registration_cutoff:
            return False
        return True
    except Exception:
        return None

def map_event_write(event) -> dict:
    return _event_base_fields(event)


def map_program_list_item(p) -> dict:
    return _program_base_fields(p)
def map_program_detail(p) -> dict:
    name=None
    if getattr(p,"enterprise",None) is not None: name=p.enterprise.business_short_name
    b=_program_base_fields(p); b["enterprise_name"]=name; return b
def map_program_write(p) -> dict:
    return _program_base_fields(p)
def _program_base_fields(p) -> dict:
    return {"id":p.id,"tenant_id":p.tenant_id,"enterprise_id":p.enterprise_id,"location_id":p.location_id,"title":p.title,"description":p.description,"category":p.category,"provider_id":p.provider_id,"duration_weeks":p.duration_weeks,"eligibility":p.eligibility,"start_date":p.start_date,"end_date":p.end_date,"enrolment_start":p.enrolment_start,"enrolment_end":p.enrolment_end,"enrol_type":p.enrol_type,"delivery_mode":p.delivery_mode,"price":p.price,"currency":p.currency,"capacity":p.capacity,"status":p.status,"is_deleted":p.is_deleted,"created_at":p.created_at,"updated_at":p.updated_at,"phases":p.phases}
def map_training_list_item(t) -> dict:
    return _training_base_fields(t)

def map_training_detail(t) -> dict:
    name = None
    if getattr(t, "enterprise", None) is not None:
        name = t.enterprise.business_short_name
    b = _training_base_fields(t); b["enterprise_name"] = name; return b

def map_training_write(t) -> dict:
    return _training_base_fields(t)

def _training_base_fields(t) -> dict:
    return {
        "id": t.id, "tenant_id": t.tenant_id, "enterprise_id": t.enterprise_id, "location_id": t.location_id,
        "title": t.title, "description": t.description, "category": t.category, "subcategory": t.subcategory,
        "tags": t.tags, "instructor_id": t.instructor_id, "requirements": t.requirements,
        "primary_image": t.primary_image, "gallery_images": t.gallery_images, "promotional_video": t.promotional_video,
        "documents": t.documents, "delivery_mode": t.delivery_mode, "course_type": t.course_type,
        "start_date": t.start_date, "end_date": t.end_date, "enrolment_start": t.enrolment_start, "enrolment_end": t.enrolment_end,
        "time_zone": t.time_zone, "capacity": t.capacity, "price": t.price, "currency": t.currency, "promo_price": t.promo_price,
        "status": t.status, "is_deleted": t.is_deleted, "created_at": t.created_at, "updated_at": t.updated_at,
        "sections": t.sections, "assessments": t.assessments, "assignments": t.assignments,
    }

def _event_base_fields(event) -> dict:
    return {
        "id": event.id,
        "tenant_id": event.tenant_id,
        "enterprise_id": event.enterprise_id,
        "location_id": event.location_id,
        "title": event.title,
        "description": event.description,
        "category": event.category,
        "subcategory": event.subcategory,
        "tags": event.tags,
        "organiser_name": event.organiser_name,
        "organiser_contact": event.organiser_contact,
        "start_date": event.start_date,
        "end_date": event.end_date,
        "duration_type": event.duration_type,
        "delivery_mode_display": {"in_person": "In Person", "online": "Online", "hybrid": "Hybrid"}.get(event.delivery_mode, event.delivery_mode),
        "time_zone": event.time_zone,
        "registration_cutoff": event.registration_cutoff,
        "primary_image": event.primary_image,
        "gallery_images": event.gallery_images,
        "videos": event.videos,
        "documents": event.documents,
        "delivery_mode": event.delivery_mode,
        "venue": event.venue,
        "meeting_link": event.meeting_link,
        "meeting_provider": event.meeting_provider,
        "price": event.price,
        "currency": event.currency,
        "ticket_types": event.ticket_types,
        "capacity": event.capacity,
        "min_participants": event.min_participants,
        "max_participants": event.max_participants,
        "registration_open_at": event.registration_open_at,
        "registration_close_at": event.registration_close_at,
        "custom_fields": event.custom_fields,
        "sessions": event.sessions,
        "status": event.status,
        "is_deleted": event.is_deleted,
        "created_at": event.created_at,
        "updated_at": event.updated_at,
        "available_seats": _event_available_seats(event),
        "is_full": _event_is_full(event),
        "registration_open": _event_registration_open(event),
    }


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
