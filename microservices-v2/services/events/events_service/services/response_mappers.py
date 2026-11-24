# v2 events copy: event (and enterprise/location) mappers only; product/service mappers live in catalog, training/program mappers in learning.
from datetime import date, timedelta
from sqlalchemy.orm import Session

from events_service.models.enterprise_model import Enterprise
from events_service.models.location_model import EnterpriseLocation
from events_service.schemas.common_schema import EnterpriseStatusLabel
from events_service.utils.event_utils import get_event_lifecycle_state
from events_service.utils.event_accommodation import resolve_event_accommodation
from events_service.utils.event_meals import resolve_event_meals
from events_service.utils.event_default_ticket import purchasable_default_ticket_id
from events_service.utils.event_modules import resolve_event_modules, resolve_event_type

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
















def map_enterprise_list_item(
    enterprise: Enterprise,
    db: Session | None = None,
    *,
    user_lat: float | None = None,
    user_lng: float | None = None,
    enrichment: dict | None = None,
) -> dict:
    base = _enterprise_base_fields(enterprise)
    base.update(
        {
            "category": enterprise.business_category,
            "status_label": enterprise_status_label(enterprise.status),
            "members_count": 0,
            "revenue": 0,
            "rating": 0,
            "joined_date": _joined_date(enterprise.created_at),
        }
    )
    if enrichment is not None:
        # Pre-batched by the caller (see get_all_enterprises_service) — avoids
        # the 2-3 per-item queries enrich_enterprise_list_fields would issue.
        base.update(enrichment)
    elif db is not None:
        base.update(
            enrich_enterprise_list_fields(
                db,
                enterprise.id,
                user_lat=user_lat,
                user_lng=user_lng,
            )
        )
    else:
        base.update({"reviews_count": 0, "distance_miles": None, "is_online": False})
    return base


def map_enterprise_detail(
    enterprise: Enterprise,
    db: Session | None = None,
    *,
    user_lat: float | None = None,
    user_lng: float | None = None,
) -> dict:
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
    if db is not None:
        base.update(
            enrich_enterprise_detail_fields(
                db,
                enterprise.id,
                email_address=enterprise.business_email,
                user_lat=user_lat,
                user_lng=user_lng,
            )
        )
    else:
        base.update(
            {
                "reviews_count": 0,
                "distance_miles": None,
                "is_online": False,
                "business_hours": [],
                "email_address": enterprise.business_email,
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
















def map_event_list_item(event, db: Session | None = None) -> dict:
    base = _event_base_fields(event)
    # Soft fallback: compute available_seats / is_full if a DB session is supplied
    if db is not None:
        try:
            from sqlalchemy import func
            from events_service.models.event_aux_models import EventRegistration
            cnt = db.scalar(
                select(func.count(EventRegistration.id))
                .join(EventRegistration.event)
                .filter(EventRegistration.status.in_(("confirmed", "attended")))
                .filter(EventRegistration.event_id == event.id)
            )
            available = (event.capacity or 0) - (cnt or 0)
            base["available_seats"] = max(available, 0)
            base["is_full"] = available <= 0
        except Exception:
            pass
    return base


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
        from events_service.utils.event_utils import is_registration_open
        return is_registration_open(event)
    except Exception:
        return None

def map_event_write(event) -> dict:
    return _event_base_fields(event)





_TRAINING_DELIVERY_MODE_ACCESS_TYPE = {
    "online": "online",
    "physical": "venue",
    "hybrid": "both",
    "self_paced": "on_demand",
    "recorded": "on_demand",
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
        "pricing_type": event.pricing_type,
        "price": event.price,
        "currency": event.currency,
        "ticket_types": event.ticket_types,
        "default_ticket_type_id": purchasable_default_ticket_id(event),
        "capacity": event.capacity,
        "min_participants": event.min_participants,
        "max_participants": event.max_participants,
        "registration_open_at": event.registration_open_at,
        "registration_close_at": event.registration_close_at,
        "custom_fields": event.custom_fields,
        "custom_values": getattr(event, "custom_values", None) or [],
        "form_configuration_id": getattr(event, "form_configuration_id", None),
        "form_configuration_version_id": getattr(event, "form_configuration_version_id", None),
        "sessions": event.sessions,
        # Configurable event (Phase 2.2): stored values when present, otherwise resolved from the event's
        # behaviour. Pure read — nothing is written back for legacy events.
        "event_type": resolve_event_type(event),
        "modules": resolve_event_modules(event),
        # Meals (Phase 2.6): enabled mirrors modules.meals; legacy events resolve to off with no options. Pure read.
        "meals": resolve_event_meals(event),
        # Accommodation (Phase 2.7): enabled mirrors modules.accommodation; legacy events resolve to off with no options. Pure read.
        "accommodation": resolve_event_accommodation(event),
        "status": event.status,
        "lifecycle_state": get_event_lifecycle_state(event),
        "is_deleted": event.is_deleted,
        "created_at": event.created_at,
        "updated_at": event.updated_at,
        "requires_reapproval": event.requires_reapproval,
        "last_admin_notes": getattr(event, "last_admin_notes", None),
        "available_seats": _event_available_seats(event),
        "is_full": _event_is_full(event),
        "registration_open": _event_registration_open(event),
    }


