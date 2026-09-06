"""Authoritative Event core field registry for the form configuration builder."""

from __future__ import annotations

from copy import deepcopy

# Stable renderer identifiers (V1)
RENDERERS = {
    "text",
    "textarea",
    "number",
    "url",
    "date",
    "datetime",
    "select",
    "multi_select",
    "checkbox",
    "tags",
    "venue",
    "ticket_types",
    "media",
    "sessions",
    "registration_fields",
}

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

# Domain-required core keys — publish validation must keep these enabled & present
DOMAIN_REQUIRED_CORE_KEYS = frozenset({"title", "description", "category", "start_date", "end_date"})

# Non-repeatable core keys
NON_REPEATABLE_CORE_KEYS = frozenset(
    {
        "title",
        "description",
        "category",
        "subcategory",
        "start_date",
        "end_date",
        "duration_type",
        "time_zone",
        "registration_open_at",
        "registration_close_at",
        "registration_cutoff",
        "delivery_mode",
        "location_id",
        "venue",
        "meeting_provider",
        "meeting_link",
        "price",
        "currency",
        "ticket_types",
        "capacity",
        "min_participants",
        "max_participants",
        "primary_image",
        "gallery_images",
        "videos",
        "documents",
        "sessions",
        "custom_fields",
        "tags",
        "organiser_name",
        "organiser_contact",
    }
)


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
        "composite_config": True,
    }
    defaults.update(kwargs)
    return defaults


def _subfield(key: str, label: str, *, required_by_default: bool = False) -> dict:
    return {"key": key, "label": label, "required_by_default": required_by_default}


def _composite(enabled: list[str], required: list[str] | None = None) -> dict:
    return {"enabled_fields": enabled, "required_fields": required or []}


# Sub-field definitions for composite Event core fields (Super Admin builder)
COMPOSITE_FIELD_DEFINITIONS: dict[str, dict] = {
    "ticket_types": {
        "subfields": [
            _subfield("id", "Ticket ID"),
            _subfield("name", "Name", required_by_default=True),
            _subfield("price", "Price", required_by_default=True),
            _subfield("currency", "Currency"),
            _subfield("capacity", "Capacity"),
        ],
        "default": _composite(["name", "price", "currency", "capacity"], ["name", "price"]),
    },
    "sessions": {
        "subfields": [
            _subfield("session_date", "Session Date"),
            _subfield("title", "Title"),
            _subfield("speaker", "Speaker"),
            _subfield("start_time", "Start Time"),
            _subfield("end_time", "End Time"),
            _subfield("location", "Location"),
        ],
        "default": _composite(["session_date", "title", "speaker", "start_time", "end_time", "location"]),
    },
    "venue": {
        "subfields": [
            _subfield("name", "Venue Name", required_by_default=True),
            _subfield("address", "Address"),
            _subfield("city", "City"),
            _subfield("coordinates.lat", "Latitude"),
            _subfield("coordinates.lng", "Longitude"),
        ],
        "default": _composite(["name", "address", "city", "coordinates.lat", "coordinates.lng"], ["name"]),
    },
    "custom_fields": {
        "subfields": [
            _subfield("label", "Question Label", required_by_default=True),
            _subfield("type", "Field Type"),
            _subfield("options", "Options"),
        ],
        "default": _composite(["label", "type", "options"], ["label"]),
    },
    "tags": {
        "subfields": [
            _subfield("freeform", "Freeform Tags"),
            _subfield("predefined_options", "Predefined Options"),
        ],
        "default": _composite(["freeform"]),
    },
    "primary_image": {
        "subfields": [
            _subfield("url", "Image URL"),
            _subfield("upload", "File Upload"),
            _subfield("alt_text", "Alt Text"),
        ],
        "default": _composite(["url", "upload"]),
    },
    "gallery_images": {
        "subfields": [
            _subfield("url", "Image URL"),
            _subfield("upload", "File Upload"),
            _subfield("caption", "Caption"),
        ],
        "default": _composite(["url", "upload", "caption"]),
    },
    "videos": {
        "subfields": [
            _subfield("url", "Video URL"),
            _subfield("upload", "File Upload"),
            _subfield("title", "Title"),
        ],
        "default": _composite(["url", "upload", "title"]),
    },
    "documents": {
        "subfields": [
            _subfield("url", "Document URL"),
            _subfield("upload", "File Upload"),
            _subfield("title", "Title"),
        ],
        "default": _composite(["url", "upload", "title"]),
    },
}

COMPOSITE_CORE_KEYS = frozenset(COMPOSITE_FIELD_DEFINITIONS.keys())


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
    if required_by_domain:
        removable = False
        hideable = False
    composite = COMPOSITE_FIELD_DEFINITIONS.get(key)
    entry = {
        "key": key,
        "display_name": display_name,
        "value_type": value_type,
        "allowed_renderers": allowed_renderers,
        "default_renderer": default_renderer,
        "required_by_domain": required_by_domain,
        "removable": removable,
        "hideable": hideable,
        "configurable": configurable or _cfg(required=not required_by_domain, renderer=not required_by_domain),
        "supports_composite_config": composite is not None,
        "composite_subfields": deepcopy(composite["subfields"]) if composite else [],
        "default_composite_config": deepcopy(composite["default"]) if composite else None,
    }
    if composite:
        entry["configurable"]["composite_config"] = True
    return entry


EVENT_FIELD_REGISTRY: list[dict] = [
    # Basic
    _entry("title", "Event Name", "string", ["text"], required_by_domain=True, configurable=_cfg(required=False, renderer=False)),
    _entry("description", "Description", "string", ["textarea"], required_by_domain=True, default_renderer="textarea", configurable=_cfg(required=False, renderer=False)),
    _entry("category", "Category", "string", ["text", "select"], required_by_domain=True, configurable=_cfg(required=False, renderer=True)),
    _entry("subcategory", "Subcategory", "string", ["text", "select"]),
    _entry("tags", "Tags", "string[]", ["tags"], default_renderer="tags", configurable=_cfg(renderer=False)),
    _entry("organiser_name", "Organiser Name", "string", ["text"]),
    _entry("organiser_contact", "Organiser Contact", "string", ["text"]),
    # Schedule
    _entry("start_date", "Start Date", "datetime", ["datetime"], required_by_domain=True, default_renderer="datetime", configurable=_cfg(required=False, renderer=False)),
    _entry("end_date", "End Date", "datetime", ["datetime"], required_by_domain=True, default_renderer="datetime", configurable=_cfg(required=False, renderer=False)),
    _entry("duration_type", "Duration Type", "string", ["select"], default_renderer="select"),
    _entry("time_zone", "Time Zone", "string", ["text", "select"], default_renderer="text"),
    _entry("registration_open_at", "Registration Opens", "datetime", ["datetime"], default_renderer="datetime"),
    _entry("registration_close_at", "Registration Closes", "datetime", ["datetime"], default_renderer="datetime"),
    _entry("registration_cutoff", "Registration Cutoff", "datetime", ["datetime"], default_renderer="datetime"),
    # Location
    _entry("delivery_mode", "Delivery Mode", "string", ["select"], default_renderer="select", configurable=_cfg(renderer=True)),
    _entry("location_id", "Location", "string", ["select"], default_renderer="select"),
    _entry("venue", "Venue", "object", ["venue"], default_renderer="venue", removable=False, hideable=True, configurable=_cfg(renderer=False, required=True)),
    _entry("meeting_provider", "Meeting Provider", "string", ["select"], default_renderer="select"),
    _entry("meeting_link", "Meeting Link", "string", ["url"], default_renderer="url"),
    # Pricing
    _entry("price", "Price", "string", ["text", "number"], default_renderer="text"),
    _entry("currency", "Currency", "string", ["text", "select"], default_renderer="text"),
    _entry("ticket_types", "Ticket Types", "array", ["ticket_types"], default_renderer="ticket_types", configurable=_cfg(renderer=False)),
    _entry("capacity", "Capacity", "string", ["number", "text"], default_renderer="number"),
    _entry("min_participants", "Minimum Participants", "string", ["number", "text"], default_renderer="number"),
    _entry("max_participants", "Maximum Participants", "string", ["number", "text"], default_renderer="number"),
    # Media
    _entry("primary_image", "Primary Image", "string", ["url", "media"], default_renderer="url"),
    _entry("gallery_images", "Gallery Images", "string[]", ["media"], default_renderer="media", configurable=_cfg(renderer=False)),
    _entry("videos", "Videos", "string[]", ["media"], default_renderer="media", configurable=_cfg(renderer=False)),
    _entry("documents", "Documents", "string[]", ["media"], default_renderer="media", configurable=_cfg(renderer=False)),
    # Additional
    _entry("sessions", "Sessions / Agenda", "array", ["sessions"], default_renderer="sessions", configurable=_cfg(renderer=False)),
    _entry(
        "custom_fields",
        "Registration Questions",
        "array",
        ["registration_fields"],
        default_renderer="registration_fields",
        removable=False,
        configurable=_cfg(renderer=False, required=True),
    ),
]

REGISTRY_BY_KEY = {item["key"]: item for item in EVENT_FIELD_REGISTRY}

DELIVERY_MODES = ("in_person", "online", "hybrid")
MEETING_PROVIDERS = ("zoom", "google_meet", "teams", "other")
DURATION_TYPES = ("one_day", "half_day", "custom")

# Fixed IDs for seeded legacy/default global configuration (deterministic fallback)
LEGACY_CONFIGURATION_ID = "00000000-0000-4000-8000-000000000001"
LEGACY_VERSION_ID = "00000000-0000-4000-8000-000000000002"


def get_composite_definition(core_key: str) -> dict | None:
    definition = COMPOSITE_FIELD_DEFINITIONS.get(core_key)
    return deepcopy(definition) if definition else None


def get_allowed_composite_subfields(core_key: str) -> frozenset[str]:
    definition = COMPOSITE_FIELD_DEFINITIONS.get(core_key)
    if not definition:
        return frozenset()
    return frozenset(sf["key"] for sf in definition["subfields"])


def normalize_composite_config(core_key: str | None, raw: dict | None) -> dict | None:
    if not core_key or core_key not in COMPOSITE_FIELD_DEFINITIONS:
        return None
    definition = COMPOSITE_FIELD_DEFINITIONS[core_key]
    default = deepcopy(definition["default"])
    allowed = get_allowed_composite_subfields(core_key)
    if not raw:
        return default
    enabled = [f for f in (raw.get("enabled_fields") or default["enabled_fields"]) if f in allowed]
    if not enabled:
        enabled = list(default["enabled_fields"])
    required = [f for f in (raw.get("required_fields") or []) if f in allowed]
    return {"enabled_fields": enabled, "required_fields": required}


def get_field_registry() -> list[dict]:
    return deepcopy(EVENT_FIELD_REGISTRY)


def get_registry_entry(core_key: str) -> dict | None:
    entry = REGISTRY_BY_KEY.get(core_key)
    return deepcopy(entry) if entry else None


def build_default_sections() -> list[dict]:
    """Initial seeded configuration mirroring the current Event create form."""

    def core_field(core_key: str, label: str, renderer: str, position: int, required: bool = False, **extra) -> dict:
        reg = REGISTRY_BY_KEY[core_key]
        field = {
            "source": "core",
            "core_key": core_key,
            "label": label,
            "renderer": renderer,
            "value_type": reg["value_type"],
            "required": required or reg["required_by_domain"],
            "is_enabled": True,
            "position": position,
            "placeholder": None,
            "help_text": None,
            "options": [],
            "validation": {},
            **extra,
        }
        composite_config = normalize_composite_config(core_key, extra.get("composite_config"))
        if composite_config is not None:
            field["composite_config"] = composite_config
        return field

    return [
        {
            "stable_key": "section_basic",
            "label": "Basic Information",
            "description": "Core details about your event",
            "position": 1,
            "is_enabled": True,
            "fields": [
                core_field("title", "Event Name", "text", 1, required=True, validation={"min_length": 3, "max_length": 255}),
                core_field("description", "Description", "textarea", 2, required=True),
                core_field("category", "Category", "select", 3, required=True),
                core_field("subcategory", "Subcategory", "select", 4),
                core_field("tags", "Tags", "tags", 5, composite_config={"enabled_fields": ["freeform"], "required_fields": []}),
                core_field("organiser_name", "Organiser Name", "text", 6),
                core_field("organiser_contact", "Organiser Contact", "text", 7),
            ],
        },
        {
            "stable_key": "section_schedule",
            "label": "Schedule",
            "description": "When your event takes place",
            "position": 2,
            "is_enabled": True,
            "fields": [
                core_field("start_date", "Start Date & Time", "datetime", 1, required=True),
                core_field("end_date", "End Date & Time", "datetime", 2, required=True),
                core_field("duration_type", "Duration Type", "select", 3, options=[
                    {"value": "one_day", "label": "One Day", "position": 1},
                    {"value": "half_day", "label": "Half Day", "position": 2},
                    {"value": "custom", "label": "Custom", "position": 3},
                ]),
                core_field("time_zone", "Time Zone", "text", 4),
                core_field("registration_open_at", "Registration Opens", "datetime", 5),
                core_field("registration_close_at", "Registration Closes", "datetime", 6),
                core_field("registration_cutoff", "Registration Cutoff", "datetime", 7),
            ],
        },
        {
            "stable_key": "section_location",
            "label": "Location & Host",
            "description": "Where and how the event is delivered",
            "position": 3,
            "is_enabled": True,
            "fields": [
                core_field("delivery_mode", "Delivery Mode", "select", 1, options=[
                    {"value": "in_person", "label": "In Person", "position": 1},
                    {"value": "online", "label": "Online", "position": 2},
                    {"value": "hybrid", "label": "Hybrid", "position": 3},
                ]),
                core_field("location_id", "Enterprise Location", "select", 2),
                core_field(
                    "venue",
                    "Venue",
                    "venue",
                    3,
                    composite_config={
                        "enabled_fields": ["name", "address", "city", "coordinates.lat", "coordinates.lng"],
                        "required_fields": ["name"],
                    },
                ),
                core_field("meeting_provider", "Meeting Provider", "select", 4, options=[
                    {"value": "zoom", "label": "Zoom", "position": 1},
                    {"value": "google_meet", "label": "Google Meet", "position": 2},
                    {"value": "teams", "label": "Microsoft Teams", "position": 3},
                    {"value": "other", "label": "Other", "position": 4},
                ]),
                core_field("meeting_link", "Meeting Link", "url", 5),
            ],
        },
        {
            "stable_key": "section_pricing",
            "label": "Pricing & Tickets",
            "description": "Pricing, tickets, and capacity",
            "position": 4,
            "is_enabled": True,
            "fields": [
                core_field("price", "Price", "text", 1),
                core_field("currency", "Currency", "text", 2),
                core_field(
                    "ticket_types",
                    "Ticket Types",
                    "ticket_types",
                    3,
                    composite_config={
                        "enabled_fields": ["name", "price", "currency", "capacity"],
                        "required_fields": ["name", "price"],
                    },
                ),
                core_field("capacity", "Capacity", "number", 4),
                core_field("min_participants", "Minimum Participants", "number", 5),
                core_field("max_participants", "Maximum Participants", "number", 6),
            ],
        },
        {
            "stable_key": "section_media",
            "label": "Media",
            "description": "Images, videos, and documents",
            "position": 5,
            "is_enabled": True,
            "fields": [
                core_field("primary_image", "Primary Image", "url", 1, composite_config={"enabled_fields": ["url", "upload"], "required_fields": []}),
                core_field("gallery_images", "Gallery Images", "media", 2, composite_config={"enabled_fields": ["url", "upload", "caption"], "required_fields": []}),
                core_field("videos", "Videos", "media", 3, composite_config={"enabled_fields": ["url", "upload", "title"], "required_fields": []}),
                core_field("documents", "Documents", "media", 4, composite_config={"enabled_fields": ["url", "upload", "title"], "required_fields": []}),
            ],
        },
        {
            "stable_key": "section_additional",
            "label": "Additional",
            "description": "Agenda sessions and registration questions",
            "position": 6,
            "is_enabled": True,
            "fields": [
                core_field(
                    "sessions",
                    "Sessions / Agenda",
                    "sessions",
                    1,
                    composite_config={
                        "enabled_fields": ["session_date", "title", "speaker", "start_time", "end_time", "location"],
                        "required_fields": [],
                    },
                ),
                core_field(
                    "custom_fields",
                    "Registration Questions",
                    "registration_fields",
                    2,
                    composite_config={"enabled_fields": ["label", "type", "options"], "required_fields": ["label"]},
                ),
            ],
        },
    ]
