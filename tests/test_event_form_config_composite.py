import pytest
from fastapi import HTTPException

from app.services.event_form_config_service import normalize_sections, validate_sections_for_publish
from app.services.event_form_registry import build_default_sections, get_field_registry, normalize_composite_config


def test_registry_exposes_composite_metadata():
    ticket = next(entry for entry in get_field_registry() if entry["key"] == "ticket_types")
    assert ticket["supports_composite_config"] is True
    assert {sf["key"] for sf in ticket["composite_subfields"]} == {"id", "name", "price", "currency", "capacity"}
    assert ticket["default_composite_config"]["required_fields"] == ["name", "price"]


def test_default_sections_include_composite_config():
    sections = build_default_sections()
    ticket = next(
        f for s in sections for f in s["fields"] if f.get("core_key") == "ticket_types"
    )
    assert ticket["composite_config"]["enabled_fields"] == ["name", "price", "currency", "capacity"]
    assert ticket["composite_config"]["required_fields"] == ["name", "price"]


def test_normalize_sections_persists_composite_config():
    sections = [
        {
            "stable_key": "section_pricing",
            "label": "Pricing",
            "position": 1,
            "is_enabled": True,
            "fields": [
                {
                    "source": "core",
                    "core_key": "sessions",
                    "label": "Sessions",
                    "renderer": "sessions",
                    "position": 1,
                    "is_enabled": True,
                    "composite_config": {
                        "enabled_fields": ["session_date", "title", "location"],
                        "required_fields": ["title"],
                    },
                }
            ],
        }
    ]
    normalized = normalize_sections(sections, assign_ids=False)
    composite = normalized[0]["fields"][0]["composite_config"]
    assert composite["enabled_fields"] == ["session_date", "title", "location"]
    assert composite["required_fields"] == ["title"]


def test_normalize_sections_rejects_unknown_subfields():
    sections = [
        {
            "stable_key": "section_pricing",
            "label": "Pricing",
            "position": 1,
            "is_enabled": True,
            "fields": [
                {
                    "source": "core",
                    "core_key": "ticket_types",
                    "label": "Ticket Types",
                    "renderer": "ticket_types",
                    "position": 1,
                    "is_enabled": True,
                    "composite_config": {
                        "enabled_fields": ["name", "invalid_subfield"],
                        "required_fields": [],
                    },
                }
            ],
        }
    ]
    normalized = normalize_sections(sections, assign_ids=False)
    assert normalized[0]["fields"][0]["composite_config"]["enabled_fields"] == ["name"]


def test_publish_validation_requires_enabled_subfields_subset():
    sections = normalize_sections(
        [
            {
                "stable_key": "section_additional",
                "label": "Additional",
                "position": 1,
                "is_enabled": True,
                "fields": [
                    {
                        "source": "core",
                        "core_key": "title",
                        "label": "Event Name",
                        "renderer": "text",
                        "position": 1,
                        "required": True,
                        "is_enabled": True,
                    },
                    {
                        "source": "core",
                        "core_key": "description",
                        "label": "Description",
                        "renderer": "textarea",
                        "position": 2,
                        "required": True,
                        "is_enabled": True,
                    },
                    {
                        "source": "core",
                        "core_key": "category",
                        "label": "Category",
                        "renderer": "select",
                        "position": 3,
                        "required": True,
                        "is_enabled": True,
                    },
                    {
                        "source": "core",
                        "core_key": "start_date",
                        "label": "Start",
                        "renderer": "datetime",
                        "position": 4,
                        "required": True,
                        "is_enabled": True,
                    },
                    {
                        "source": "core",
                        "core_key": "end_date",
                        "label": "End",
                        "renderer": "datetime",
                        "position": 5,
                        "required": True,
                        "is_enabled": True,
                    },
                    {
                        "source": "core",
                        "core_key": "sessions",
                        "label": "Sessions",
                        "renderer": "sessions",
                        "position": 6,
                        "is_enabled": True,
                        "composite_config": {
                            "enabled_fields": ["title"],
                            "required_fields": ["speaker"],
                        },
                    },
                ],
            }
        ],
        assign_ids=False,
    )
    with pytest.raises(HTTPException) as exc:
        validate_sections_for_publish(sections, scope="global")
    assert "Required sub-fields must be enabled" in str(exc.value.detail)


def test_normalize_composite_config_defaults():
    cfg = normalize_composite_config("venue", None)
    assert "name" in cfg["enabled_fields"]
    assert cfg["required_fields"] == ["name"]
