"""Authoritative Training core field registry for the form configuration builder."""

from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

CUSTOM_RENDERERS = {
    "text",
    "textarea",
    "number",
    "url",
    "date",
    "datetime",
    "select",
    "multi_select",
    "checkbox",
}

# Seed + publish defaults — title/category required by domain; price optional but present
DOMAIN_REQUIRED_CORE_KEYS = frozenset({"title", "category"})

NON_REPEATABLE_CORE_KEYS = frozenset(
    {
        "title",
        "description",
        "category",
        "subcategory",
        "delivery_mode",
        "course_type",
        "duration",
        "start_date",
        "end_date",
        "enrolment_start",
        "enrolment_end",
        "time_zone",
        "capacity",
        "price",
        "currency",
        "promo_price",
        "coupon_code",
        "requires_approval",
        "access_duration_days",
        "instructor_id",
        "requirements",
        "primary_image",
        "promotional_video",
        "location_id",
    }
)

# Stable seed IDs (global active default)
DEFAULT_CONFIGURATION_ID = "00000000-0000-4000-8000-000000000011"
DEFAULT_VERSION_ID = "00000000-0000-4000-8000-000000000012"


def _cfg(**kwargs) -> dict:
    defaults = {
        "label": True,
        "section": True,
        "position": True,
        "required": True,
        "renderer": True,
        "placeholder": True,
        "help_text": True,
        "validation": True,
        "composite_config": False,
    }
    defaults.update(kwargs)
    return defaults


def _entry(
    key: str,
    display_name: str,
    value_type: str,
    allowed_renderers: list[str],
    *,
    required_by_domain: bool = False,
    removable: bool = True,
    hideable: bool = True,
    configurable: dict | None = None,
    default_renderer: str = "text",
) -> dict:
    return {
        "key": key,
        "display_name": display_name,
        "value_type": value_type,
        "allowed_renderers": allowed_renderers,
        "default_renderer": default_renderer,
        "required_by_domain": required_by_domain,
        "removable": removable if not required_by_domain else False,
        "hideable": hideable if not required_by_domain else False,
        "configurable": configurable or _cfg(),
        "supports_composite_config": False,
        "composite_subfields": [],
        "default_composite_config": None,
    }


FIELD_REGISTRY: list[dict] = [
    _entry("title", "Title", "string", ["text", "textarea"], required_by_domain=True, removable=False, hideable=False),
    _entry("description", "Description", "string", ["textarea", "text"]),
    _entry("category", "Category", "string", ["text", "select"], required_by_domain=True, removable=False, hideable=False),
    _entry("subcategory", "Subcategory", "string", ["text", "select"]),
    _entry("delivery_mode", "Delivery Mode", "string", ["select", "text"], default_renderer="select"),
    _entry("course_type", "Course Type", "string", ["select", "text"], default_renderer="select"),
    _entry("duration", "Duration", "string", ["text"]),
    _entry("start_date", "Start Date", "datetime", ["datetime", "date"], default_renderer="datetime"),
    _entry("end_date", "End Date", "datetime", ["datetime", "date"], default_renderer="datetime"),
    _entry("enrolment_start", "Enrolment Start", "datetime", ["datetime", "date"], default_renderer="datetime"),
    _entry("enrolment_end", "Enrolment End", "datetime", ["datetime", "date"], default_renderer="datetime"),
    _entry("time_zone", "Time Zone", "string", ["text", "select"]),
    _entry("capacity", "Capacity", "string", ["text", "number"]),
    _entry("price", "Price", "string", ["text", "number"]),
    _entry("currency", "Currency", "string", ["text", "select"]),
    _entry("promo_price", "Promo Price", "string", ["text", "number"]),
    _entry("coupon_code", "Coupon Code", "string", ["text"]),
    _entry("requires_approval", "Requires Approval", "boolean", ["checkbox"], default_renderer="checkbox"),
    _entry("access_duration_days", "Access Duration (days)", "string", ["text", "number"]),
    _entry("instructor_id", "Instructor", "string", ["text"]),
    _entry("requirements", "Requirements", "string", ["textarea", "text"], default_renderer="textarea"),
    _entry("primary_image", "Primary Image", "string", ["url", "text"], default_renderer="url"),
    _entry("promotional_video", "Promotional Video", "string", ["url", "text"], default_renderer="url"),
    _entry("location_id", "Location", "string", ["text"]),
]

REGISTRY_BY_KEY = {e["key"]: e for e in FIELD_REGISTRY}


def get_field_registry() -> list[dict]:
    return deepcopy(FIELD_REGISTRY)


def _core_field(
    core_key: str,
    label: str,
    renderer: str,
    position: int,
    *,
    required: bool = False,
    options: list | None = None,
) -> dict:
    meta = REGISTRY_BY_KEY[core_key]
    return {
        "id": f"field_{core_key}",
        "source": "core",
        "core_key": core_key,
        "stable_key": core_key,
        "label": label,
        "renderer": renderer,
        "value_type": meta["value_type"],
        "required": required,
        "is_enabled": True,
        "position": position,
        "placeholder": None,
        "help_text": None,
        "options": options or [],
        "validation": {},
        "composite_config": None,
    }


def build_default_sections() -> list[dict]:
    """Full default builder sections (all common Training fields)."""
    return [
        {
            "id": "section_basic",
            "stable_key": "basic",
            "label": "Basic Information",
            "description": "Core training details",
            "position": 1,
            "is_enabled": True,
            "fields": [
                _core_field("title", "Title", "text", 1, required=True),
                _core_field("description", "Description", "textarea", 2),
                _core_field("category", "Category", "text", 3, required=True),
                _core_field("subcategory", "Subcategory", "text", 4),
                _core_field("delivery_mode", "Delivery Mode", "select", 5, options=[
                    {"value": "self_paced", "label": "Self Paced", "position": 1},
                    {"value": "instructor_led", "label": "Instructor Led", "position": 2},
                    {"value": "blended", "label": "Blended", "position": 3},
                ]),
            ],
        },
        {
            "id": "section_pricing",
            "stable_key": "pricing",
            "label": "Pricing & Capacity",
            "description": None,
            "position": 2,
            "is_enabled": True,
            "fields": [
                _core_field("price", "Price", "text", 1),
                _core_field("currency", "Currency", "text", 2),
                _core_field("capacity", "Capacity", "text", 3),
            ],
        },
    ]


def build_seed_sections() -> list[dict]:
    """Minimal seeded global form: 1 section, title / category / price."""
    return [
        {
            "id": "section_basic",
            "stable_key": "basic",
            "label": "Training Details",
            "description": "Super Admin default Training form (Global)",
            "position": 1,
            "is_enabled": True,
            "fields": [
                _core_field("title", "Title", "text", 1, required=True),
                _core_field("category", "Category", "text", 2, required=True),
                _core_field("price", "Price", "text", 3),
            ],
        }
    ]


def normalize_sections(sections: list[dict], *, assign_ids: bool = True) -> list[dict]:
    normalized: list[dict] = []
    for idx, section in enumerate(sections or [], start=1):
        if not isinstance(section, dict):
            continue
        sid = section.get("id") or (f"section_{uuid4().hex[:12]}" if assign_ids else section.get("stable_key") or f"section_{idx}")
        fields_out: list[dict] = []
        for fidx, field in enumerate(section.get("fields") or [], start=1):
            if not isinstance(field, dict):
                continue
            source = field.get("source") or "custom"
            core_key = field.get("core_key")
            fid = field.get("id") or (f"field_{core_key}" if core_key else f"field_{uuid4().hex[:12]}")
            if assign_ids and not field.get("id"):
                fid = f"field_{uuid4().hex[:12]}" if source == "custom" else (f"field_{core_key}" if core_key else fid)
            meta = REGISTRY_BY_KEY.get(core_key or "", {})
            fields_out.append(
                {
                    "id": fid,
                    "source": source,
                    "core_key": core_key,
                    "stable_key": field.get("stable_key") or core_key or field.get("label"),
                    "label": field.get("label") or meta.get("display_name") or "Field",
                    "renderer": field.get("renderer") or meta.get("default_renderer") or "text",
                    "value_type": field.get("value_type") or meta.get("value_type") or "string",
                    "required": bool(field.get("required", False)),
                    "is_enabled": field.get("is_enabled", True) is not False,
                    "position": int(field.get("position") or fidx),
                    "placeholder": field.get("placeholder"),
                    "help_text": field.get("help_text"),
                    "options": field.get("options") or [],
                    "validation": field.get("validation") or {},
                    "composite_config": field.get("composite_config"),
                }
            )
        fields_out.sort(key=lambda f: f["position"])
        normalized.append(
            {
                "id": sid,
                "stable_key": section.get("stable_key") or f"section_{idx}",
                "label": section.get("label") or f"Section {idx}",
                "description": section.get("description"),
                "position": int(section.get("position") or idx),
                "is_enabled": section.get("is_enabled", True) is not False,
                "fields": fields_out,
            }
        )
    normalized.sort(key=lambda s: s["position"])
    return normalized


def validate_sections_for_publish(sections: list[dict], *, scope: str) -> None:
    from fastapi import HTTPException

    if not sections:
        raise HTTPException(status_code=400, detail="At least one section is required to publish")

    seen_cores: set[str] = set()
    enabled_cores: set[str] = set()
    for section in sections:
        if not section.get("is_enabled", True):
            continue
        for field in section.get("fields") or []:
            if not field.get("is_enabled", True):
                continue
            renderer = field.get("renderer")
            source = field.get("source")
            if source == "custom" and renderer not in CUSTOM_RENDERERS:
                raise HTTPException(status_code=400, detail=f"Invalid custom renderer: {renderer}")
            if source == "core":
                key = field.get("core_key")
                if not key or key not in REGISTRY_BY_KEY:
                    raise HTTPException(status_code=400, detail=f"Unknown core_key: {key}")
                if key in seen_cores:
                    raise HTTPException(status_code=400, detail=f"Duplicate core field: {key}")
                seen_cores.add(key)
                enabled_cores.add(key)
                allowed = set(REGISTRY_BY_KEY[key]["allowed_renderers"])
                if renderer not in allowed:
                    raise HTTPException(status_code=400, detail=f"Renderer '{renderer}' not allowed for {key}")

    missing = DOMAIN_REQUIRED_CORE_KEYS - enabled_cores
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Domain-required core fields must be enabled: {sorted(missing)}",
        )
