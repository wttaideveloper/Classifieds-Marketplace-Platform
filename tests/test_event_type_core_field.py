"""Fix: Event Form Configuration publish validation rejected `event_type`
with "Unknown core field: 'event_type'" even though the backend already has
a dynamic, DB-backed Event Type registry (GET /api/v1/event-types/, model
EventTypeConfig — see app/models/event_type_model.py). This registers
`event_type` in the Form Configuration core-field registry, resolving its
options live from that existing registry (never a second/static list, and
never folded into the Delivery Mode bundle), and proves publish validation
now accepts it while everything else — most importantly delivery_mode — is
left untouched."""

import pytest
from fastapi import HTTPException

from app.schemas.event_form_config_schema import FieldRegistryEntry
from app.services.event_form_config_service import normalize_sections, validate_sections_for_publish
from app.services.event_form_registry import (
    COMPOSITE_CORE_KEYS,
    DOMAIN_REQUIRED_CORE_KEYS,
    NON_REPEATABLE_CORE_KEYS,
    REGISTRY_BY_KEY,
    get_field_registry,
)


def test_event_type_is_registered_as_a_core_field():
    assert "event_type" in REGISTRY_BY_KEY
    assert "event_type" in {e["key"] for e in get_field_registry()}


def test_event_type_field_metadata_matches_the_required_contract():
    e = REGISTRY_BY_KEY["event_type"]
    assert e["value_type"] == "string"
    assert e["allowed_renderers"] == ["select"]
    assert e["default_renderer"] == "select"
    assert e["required_by_domain"] is False


def test_event_type_options_are_resolved_from_the_event_type_registry_not_hardcoded():
    """Option value = Event Type `key`, option label = `name` — resolved live
    from GET /api/v1/event-types/ (the existing registry), never a second,
    static Form Configuration list."""
    e = REGISTRY_BY_KEY["event_type"]
    assert e["value_source"] == "event_types"
    assert e["source_endpoint"] == "/api/v1/event-types/"
    assert e["options"] is None  # dynamic — no static/duplicated list in the registry


def test_event_type_not_domain_required_and_not_composite():
    assert "event_type" not in DOMAIN_REQUIRED_CORE_KEYS
    assert "event_type" not in COMPOSITE_CORE_KEYS


def test_event_type_is_non_repeatable_like_every_other_core_key():
    assert "event_type" in NON_REPEATABLE_CORE_KEYS


def test_normalize_sections_leaves_event_type_options_empty_when_unspecified():
    """Same dynamic pattern as category/location_id: no static fallback list
    gets baked in at normalize/publish time — the frontend resolves options
    live from source_endpoint."""
    sections = [
        {
            "id": "s1", "stable_key": "section_basic", "label": "Basic", "position": 1, "is_enabled": True,
            "fields": [
                {"id": "f1", "source": "core", "core_key": "event_type", "label": "Event Type",
                 "renderer": "select", "position": 1, "is_enabled": True},
            ],
        }
    ]
    out = normalize_sections(sections, assign_ids=False)
    assert out[0]["fields"][0]["options"] == []


def _domain_required_fields():
    """The minimal set validate_sections_for_publish requires present+enabled
    for scope='global' (DOMAIN_REQUIRED_CORE_KEYS), independent of event_type."""
    specs = [
        ("title", "text"), ("description", "textarea"), ("category", "select"),
        ("start_date", "datetime"), ("end_date", "datetime"), ("pricing_type", "select"),
    ]
    return [
        {"id": f"f{i}", "source": "core", "core_key": key, "label": key, "renderer": renderer,
         "position": i, "required": True, "is_enabled": True}
        for i, (key, renderer) in enumerate(specs, start=1)
    ]


def test_publish_no_longer_rejects_event_type_as_unknown_core_field():
    """Reproduces the exact reported failure: 'Unknown core field: event_type'."""
    fields = _domain_required_fields()
    fields.append({
        "id": "f_event_type", "source": "core", "core_key": "event_type", "label": "Event Type",
        "renderer": "select", "position": len(fields) + 1, "required": False, "is_enabled": True,
    })
    sections = normalize_sections(
        [{"stable_key": "section_basic", "label": "Basic", "position": 1, "is_enabled": True, "fields": fields}],
        assign_ids=False,
    )
    validate_sections_for_publish(sections, scope="global")  # must not raise


def test_publish_still_rejects_a_genuinely_unknown_core_field():
    """Sibling regression guard: the fix must not have disabled unknown-core-key
    detection for everything else."""
    fields = _domain_required_fields()
    fields.append({
        "id": "f_bogus", "source": "core", "core_key": "not_a_real_core_key", "label": "Bogus",
        "renderer": "text", "position": len(fields) + 1, "required": False, "is_enabled": True,
    })
    sections = normalize_sections(
        [{"stable_key": "section_basic", "label": "Basic", "position": 1, "is_enabled": True, "fields": fields}],
        assign_ids=False,
    )
    with pytest.raises(HTTPException) as exc:
        validate_sections_for_publish(sections, scope="global")
    assert "Unknown core field: 'not_a_real_core_key'" in str(exc.value.detail)


def test_every_registry_entry_validates_against_the_field_registry_response_schema():
    """Every entry get_field_registry() returns — including the new event_type
    one — must still validate against the OpenAPI-exposed FieldRegistryEntry
    response model (catches JSON-contract shape drift, not just the raw dict)."""
    for entry in get_field_registry():
        FieldRegistryEntry(**entry)


def test_event_type_is_completely_separate_from_delivery_mode():
    """Explicit requirement: event_type must never be folded into the
    Delivery Mode bundle, and must not duplicate/alter its static options."""
    event_type = REGISTRY_BY_KEY["event_type"]
    delivery_mode = REGISTRY_BY_KEY["delivery_mode"]
    assert event_type["key"] != delivery_mode["key"]
    assert delivery_mode["value_source"] == "static"
    assert {o["value"] for o in delivery_mode["options"]} == {"in_person", "online", "hybrid"}
    assert event_type["value_source"] != delivery_mode["value_source"]
    assert event_type.get("options") is None
