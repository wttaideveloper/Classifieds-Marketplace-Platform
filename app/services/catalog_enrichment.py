"""Computed catalog fields for enterprise, product, and service API responses."""

from __future__ import annotations

import json
import math
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.attribute_model import DynamicAttribute
from app.models.location_model import EnterpriseLocation
from app.models.service_model import Service

_DAY_ORDER = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]

_DAY_LABELS = {
    "monday": "Monday",
    "tuesday": "Tuesday",
    "wednesday": "Wednesday",
    "thursday": "Thursday",
    "friday": "Friday",
    "saturday": "Saturday",
    "sunday": "Sunday",
}

_LISTING_TYPE_LABELS = {
    "subscription": "Subscription",
    "one_time": "One-time",
    "one-time": "One-time",
}


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_miles = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return round(radius_miles * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 1)


def nearest_location_distance_miles(
    db: Session,
    enterprise_id: UUID,
    user_lat: float | None,
    user_lng: float | None,
) -> float | None:
    if user_lat is None or user_lng is None:
        return None

    locations = (
        db.query(EnterpriseLocation)
        .filter(
            EnterpriseLocation.enterprise_id == enterprise_id,
            EnterpriseLocation.is_deleted.is_(False),
            EnterpriseLocation.latitude.isnot(None),
            EnterpriseLocation.longitude.isnot(None),
        )
        .all()
    )
    if not locations:
        return None

    distances = [
        haversine_miles(user_lat, user_lng, loc.latitude, loc.longitude)
        for loc in locations
        if loc.latitude is not None and loc.longitude is not None
    ]
    return min(distances) if distances else None


def aggregate_business_hours(db: Session, enterprise_id: UUID) -> list[dict]:
    services = (
        db.query(Service)
        .filter(
            Service.enterprise_id == enterprise_id,
            Service.is_deleted.is_(False),
            Service.status == "active",
        )
        .all()
    )

    day_windows: dict[str, dict[str, str | None]] = {
        day: {"open": None, "close": None, "is_closed": True}
        for day in _DAY_ORDER
    }

    for service in services:
        schedule = service.availability_schedule or []
        if not isinstance(schedule, list):
            continue
        for entry in schedule:
            if not isinstance(entry, dict) or not entry.get("is_available", True):
                continue
            day_key = str(entry.get("day", "")).strip().lower()
            if day_key not in day_windows:
                continue
            start_time = entry.get("start_time")
            end_time = entry.get("end_time")
            if not start_time or not end_time:
                continue

            window = day_windows[day_key]
            window["is_closed"] = False
            if window["open"] is None or start_time < window["open"]:
                window["open"] = start_time
            if window["close"] is None or end_time > window["close"]:
                window["close"] = end_time

    return [
        {
            "day": _DAY_LABELS[day],
            "open": day_windows[day]["open"],
            "close": day_windows[day]["close"],
            "is_closed": day_windows[day]["is_closed"],
        }
        for day in _DAY_ORDER
    ]


def is_open_now(business_hours: list[dict]) -> bool:
    now = datetime.utcnow()
    today = _DAY_ORDER[now.weekday()]
    today_label = _DAY_LABELS[today]

    entry = next((row for row in business_hours if row.get("day") == today_label), None)
    if not entry or entry.get("is_closed"):
        return False

    open_time = entry.get("open")
    close_time = entry.get("close")
    if not open_time or not close_time:
        return False

    try:
        open_parts = [int(part) for part in str(open_time).split(":", 1)]
        close_parts = [int(part) for part in str(close_time).split(":", 1)]
        now_minutes = now.hour * 60 + now.minute
        open_minutes = open_parts[0] * 60 + open_parts[1]
        close_minutes = close_parts[0] * 60 + close_parts[1]
        return open_minutes <= now_minutes <= close_minutes
    except (TypeError, ValueError, IndexError):
        return False


def format_listing_type(raw_value: str | None) -> str:
    if not raw_value:
        return "One-time"
    normalized = str(raw_value).strip().lower().replace(" ", "_")
    return _LISTING_TYPE_LABELS.get(normalized, raw_value.strip().title())


def build_delivery_text(
    delivery_interval: str | None,
    delivery_fee: str | None,
) -> str | None:
    parts: list[str] = []
    if delivery_interval and delivery_interval.strip():
        parts.append(delivery_interval.strip())
    if delivery_fee and delivery_fee.strip():
        parts.append(delivery_fee.strip())
    return " · ".join(parts) if parts else None


def _parse_review_payload(raw_value: str) -> list[dict]:
    try:
        parsed = json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return []

    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
        nested = parsed.get("items") or parsed.get("reviews")
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
    return []


def get_catalog_reviews(db: Session, entity_type: str, entity_id: UUID) -> tuple[list[dict], int]:
    rows = (
        db.query(DynamicAttribute)
        .filter(
            DynamicAttribute.entity_type == entity_type,
            DynamicAttribute.entity_id == entity_id,
            DynamicAttribute.attribute_name.in_(("reviews", "review")),
            DynamicAttribute.is_deleted.is_(False),
        )
        .all()
    )

    reviews: list[dict] = []
    for row in rows:
        reviews.extend(_parse_review_payload(row.attribute_value))

    normalized: list[dict] = []
    for index, item in enumerate(reviews):
        rating = item.get("rating")
        if rating is None:
            continue
        normalized.append(
            {
                "id": str(item.get("id") or f"{entity_id}-{index}"),
                "rating": int(rating),
                "comment": item.get("comment"),
                "reviewer_name": item.get("reviewer_name") or item.get("author"),
                "created_at": item.get("created_at"),
            }
        )

    return normalized, len(normalized)


def enrich_enterprise_list_fields(
    db: Session,
    enterprise_id: UUID,
    *,
    user_lat: float | None = None,
    user_lng: float | None = None,
) -> dict:
    business_hours = aggregate_business_hours(db, enterprise_id)
    _, reviews_count = get_catalog_reviews(db, "enterprise", enterprise_id)
    return {
        "reviews_count": reviews_count,
        "distance_miles": nearest_location_distance_miles(db, enterprise_id, user_lat, user_lng),
        "is_online": is_open_now(business_hours),
    }


def enrich_enterprise_detail_fields(
    db: Session,
    enterprise_id: UUID,
    *,
    email_address: str | None,
    user_lat: float | None = None,
    user_lng: float | None = None,
) -> dict:
    business_hours = aggregate_business_hours(db, enterprise_id)
    _, reviews_count = get_catalog_reviews(db, "enterprise", enterprise_id)
    return {
        "reviews_count": reviews_count,
        "distance_miles": nearest_location_distance_miles(db, enterprise_id, user_lat, user_lng),
        "is_online": is_open_now(business_hours),
        "business_hours": business_hours,
        "email_address": email_address,
    }
