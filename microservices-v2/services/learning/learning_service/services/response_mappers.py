# v2 learning copy: training and program mappers only (product/service in catalog, event in events).
from datetime import date, timedelta
from sqlalchemy.orm import Session

from learning_service.models.enterprise_model import Enterprise
from learning_service.models.location_model import EnterpriseLocation
from learning_service.schemas.common_schema import EnterpriseStatusLabel
from learning_service.utils.event_utils import get_event_lifecycle_state
from learning_service.utils.event_accommodation import resolve_event_accommodation
from learning_service.utils.event_meals import resolve_event_meals
from learning_service.utils.event_default_ticket import purchasable_default_ticket_id
from learning_service.utils.event_modules import resolve_event_modules, resolve_event_type

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

























def map_program_list_item(p) -> dict:
    return _program_base_fields(p)
def map_program_detail(p) -> dict:
    name=None
    if getattr(p,"enterprise",None) is not None: name=p.enterprise.business_short_name
    b=_program_base_fields(p); b["enterprise_name"]=name; return b
def map_program_write(p) -> dict:
    return _program_base_fields(p)
def _program_base_fields(p) -> dict:
    return {
        "id": p.id,
        "tenant_id": p.tenant_id,
        "enterprise_id": p.enterprise_id,
        "location_id": p.location_id,
        "title": p.title,
        "description": p.description,
        "category": p.category,
        "provider_id": p.provider_id,
        "duration_weeks": p.duration_weeks,
        "eligibility": p.eligibility,
        "start_date": p.start_date,
        "end_date": p.end_date,
        "enrolment_start": p.enrolment_start,
        "enrolment_end": p.enrolment_end,
        "enrol_type": p.enrol_type,
        "delivery_mode": p.delivery_mode,
        "price": p.price,
        "currency": p.currency,
        "capacity": p.capacity,
        "status": p.status,
        "is_deleted": p.is_deleted,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
        "phases": p.phases,
        "goals": getattr(p, "goals", None) or {},
        "last_admin_notes": getattr(p, "last_admin_notes", None),
        "custom_values": getattr(p, "custom_values", None) or [],
        "form_configuration_id": getattr(p, "form_configuration_id", None),
        "form_configuration_version_id": getattr(p, "form_configuration_version_id", None),
    }
def map_training_list_item(t) -> dict:
    return _training_base_fields(t)

def map_training_detail(t) -> dict:
    name = None
    if getattr(t, "enterprise", None) is not None:
        name = t.enterprise.business_short_name
    b = map_training_write(t); b["enterprise_name"] = name; return b

def map_training_write(t) -> dict:
    from learning_service.services.training_curriculum import normalize_curriculum, apply_mode_to_response
    result = _training_base_fields(t)
    result.update(normalize_curriculum(t.sections, t.assessments, t.assignments))
    return apply_mode_to_response(result)

_TRAINING_DELIVERY_MODE_ACCESS_TYPE = {
    "online": "online",
    "physical": "venue",
    "hybrid": "both",
    "self_paced": "on_demand",
    "recorded": "on_demand",
}


def _qr_image_base64(payload) -> str | None:
    if not payload:
        return None
    try:
        import base64
        import io

        import qrcode

        img = qrcode.make(payload)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def _training_base_fields(t) -> dict:
    return {
        "id": t.id, "tenant_id": t.tenant_id, "enterprise_id": t.enterprise_id, "location_id": t.location_id,
        "title": t.title, "description": t.description, "category": t.category, "subcategory": t.subcategory,
        "tags": t.tags, "instructor_id": t.instructor_id, "requirements": t.requirements,
        "instructor_name": getattr(t, "instructor_name", None),
        "instructor_bio": getattr(t, "instructor_bio", None),
        "instructor_photo": getattr(t, "instructor_photo", None),
        "instructor_credentials": getattr(t, "instructor_credentials", None),
        "prerequisites": getattr(t, "prerequisites", None) or [],
        "learning_objectives": getattr(t, "learning_objectives", None) or [],
        "target_audience": getattr(t, "target_audience", None),
        "level": getattr(t, "level", None),
        "language": getattr(t, "language", None),
        "subtitle": getattr(t, "subtitle", None),
        "primary_image": t.primary_image, "gallery_images": t.gallery_images, "promotional_video": t.promotional_video,
        "documents": t.documents, "notes_documents": getattr(t, "notes_documents", None) or [],
        "delivery_mode": t.delivery_mode, "course_type": t.course_type,
        "start_date": t.start_date, "end_date": t.end_date, "enrolment_start": t.enrolment_start, "enrolment_end": t.enrolment_end,
        "start_time": getattr(t, "start_time", None),
        "end_time": getattr(t, "end_time", None),
        "venue": getattr(t, "venue", None),
        "address": getattr(t, "address", None),
        "meeting_link": getattr(t, "meeting_link", None),
        "meeting_provider": getattr(t, "meeting_provider", None),
        "meeting_platform": getattr(t, "meeting_provider", None),
        "meeting_id": getattr(t, "meeting_id", None),
        "meeting_passcode": getattr(t, "meeting_passcode", None),
        "access_information": getattr(t, "access_information", None),
        "delivery_instructions": getattr(t, "delivery_instructions", None),
        "offline_access_enabled": getattr(t, "offline_access_enabled", None) or False,
        "recurring": getattr(t, "recurring", None),
        "schedule_exceptions": getattr(t, "schedule_exceptions", None) or [],
        "instructor_notes": getattr(t, "instructor_notes", None),
        "session_mode": getattr(t, "session_mode", None),
        "check_in": getattr(t, "check_in", None) or False,
        "pass_code": getattr(t, "pass_code", None),
        "qr_payload": getattr(t, "qr_payload", None),
        "qr_image_base64": _qr_image_base64(getattr(t, "qr_payload", None)),
        "access_type": _TRAINING_DELIVERY_MODE_ACCESS_TYPE.get(t.delivery_mode),
        "difficulty_level": getattr(t, "level", None),
        "promotional_video_url": t.promotional_video,
        "notes": getattr(t, "notes_documents", None) or [],
        "access_expiry_days": getattr(t, "access_duration_days", None),
        "moderation_status": getattr(t, "moderation_status", None),
        "rejection_reason": getattr(t, "rejection_reason", None),
        "published_at": getattr(t, "published_at", None),
        "approved_at": getattr(t, "approved_at", None),
        "archived_at": getattr(t, "archived_at", None),
        "suspended_at": getattr(t, "suspended_at", None),
        "cancelled_at": getattr(t, "cancelled_at", None),
        "release_rule": getattr(t, "release_rule", None),
        "scheduled_publication": getattr(t, "scheduled_publication", None),
        "randomise": getattr(t, "randomise", None) or False,
        "is_mandatory": getattr(t, "is_mandatory", None) or False,
        "faqs": getattr(t, "faqs", None) or [],
        "badges": getattr(t, "badges", None) or [],
        "notes_pdf_url": getattr(t, "notes_pdf_url", None) or f"/api/v1/trainings/{t.id}/notes.pdf",
        "instructor": {
            "id": t.instructor_id,
            "name": getattr(t, "instructor_name", None),
            "bio": getattr(t, "instructor_bio", None),
            "role": getattr(t, "instructor_role", None),
        },
        "time_zone": t.time_zone, "capacity": t.capacity, "price": t.price, "currency": t.currency, "promo_price": t.promo_price,
        "status": t.status, "is_deleted": t.is_deleted, "created_at": t.created_at, "updated_at": t.updated_at,
        "sections": t.sections, "assessments": t.assessments, "assignments": t.assignments,
        "requires_approval": getattr(t, "requires_approval", False),
        "access_duration_days": getattr(t, "access_duration_days", None),
        "promo_price": t.promo_price, "coupon_code": t.coupon_code,
        "duration": getattr(t, "duration", None),
        "duration_hours": getattr(t, "duration_hours", None),
        "last_admin_notes": getattr(t, "last_admin_notes", None),
        "custom_values": getattr(t, "custom_values", None) or [],
        "form_configuration_id": getattr(t, "form_configuration_id", None),
        "form_configuration_version_id": getattr(t, "form_configuration_version_id", None),
    }



