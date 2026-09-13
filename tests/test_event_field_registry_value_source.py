"""New, purely additive registry metadata (options/value_source/source_endpoint/
depends_on) for the Event core-field registry — lets the frontend render the
correct control for category/subcategory/location_id/duration_type/
delivery_mode/currency/time_zone without hardcoding, while leaving every
existing Event request/response shape, validation rule, and registry
semantic (required_by_domain/removable/hideable/renderers) untouched."""

from app.services.event_form_registry import REGISTRY_BY_KEY, get_field_registry


def test_category_exposes_event_categories_source():
    e = REGISTRY_BY_KEY["category"]
    assert e["value_source"] == "event_categories"
    assert e["source_endpoint"] == "/api/v1/event-categories/"
    assert e["depends_on"] is None
    assert e["options"] is None  # dynamic — not a static list


def test_subcategory_depends_on_category():
    e = REGISTRY_BY_KEY["subcategory"]
    assert e["value_source"] == "event_categories"
    assert e["depends_on"] == "category"


def test_location_id_exposes_enterprise_locations_source():
    e = REGISTRY_BY_KEY["location_id"]
    assert e["value_source"] == "enterprise_locations"
    assert "{enterprise_id}" in e["source_endpoint"]


def test_duration_type_exposes_exact_documented_options():
    e = REGISTRY_BY_KEY["duration_type"]
    values = [o["value"] for o in e["options"]]
    assert values == ["one_day", "half_day", "custom"]


def test_delivery_mode_exposes_the_three_documented_values():
    e = REGISTRY_BY_KEY["delivery_mode"]
    values = {o["value"] for o in e["options"]}
    assert values == {"in_person", "online", "hybrid"}


def test_currency_exposes_full_iso4217_list_with_code_and_label():
    e = REGISTRY_BY_KEY["currency"]
    assert e["value_source"] == "static"
    values = {o["value"] for o in e["options"]}
    assert "INR" in values and "USD" in values and "EUR" in values
    assert len(e["options"]) > 100  # full list, not a hand-picked subset
    inr = next(o for o in e["options"] if o["value"] == "INR")
    assert "INR" in inr["label"] and "Rupee" in inr["label"]


def test_time_zone_exposes_full_iana_list():
    e = REGISTRY_BY_KEY["time_zone"]
    assert e["value_source"] == "static"
    values = {o["value"] for o in e["options"]}
    assert {"Asia/Kolkata", "Europe/London", "America/New_York"}.issubset(values)
    assert len(e["options"]) > 300  # full IANA db, not a curated shortlist


def test_registry_entry_shape_backward_compatible_for_unrelated_fields():
    """Fields not touched by this change must carry the new keys as None —
    proving the addition doesn't alter their existing metadata."""
    e = REGISTRY_BY_KEY["title"]
    assert e["options"] is None
    assert e["value_source"] is None
    assert e["source_endpoint"] is None
    assert e["depends_on"] is None


def test_required_removable_hideable_configurable_unchanged_for_touched_fields():
    """The explicit ask: do not make optional fields mandatory, and leave
    required_by_domain/removable/hideable/renderer semantics untouched."""
    expected = {
        "category": {"required_by_domain": True, "removable": False, "hideable": False},
        "subcategory": {"required_by_domain": False, "removable": True, "hideable": True},
        "location_id": {"required_by_domain": False, "removable": True, "hideable": True},
        "duration_type": {"required_by_domain": False, "removable": True, "hideable": True},
        "delivery_mode": {"required_by_domain": False, "removable": True, "hideable": True},
        "currency": {"required_by_domain": False, "removable": True, "hideable": True},
        "time_zone": {"required_by_domain": False, "removable": True, "hideable": True},
    }
    for key, expected_flags in expected.items():
        e = REGISTRY_BY_KEY[key]
        for flag, value in expected_flags.items():
            assert e[flag] == value, f"{key}.{flag} changed: expected {value}, got {e[flag]}"

    # Renderers (allowed_renderers / default_renderer) also unchanged
    assert REGISTRY_BY_KEY["category"]["allowed_renderers"] == ["text", "select"]
    assert REGISTRY_BY_KEY["duration_type"]["allowed_renderers"] == ["select"]
    assert REGISTRY_BY_KEY["duration_type"]["default_renderer"] == "select"
    assert REGISTRY_BY_KEY["currency"]["allowed_renderers"] == ["text", "select"]
    assert REGISTRY_BY_KEY["currency"]["default_renderer"] == "text"
    assert REGISTRY_BY_KEY["time_zone"]["allowed_renderers"] == ["text", "select"]
    assert REGISTRY_BY_KEY["time_zone"]["default_renderer"] == "text"
    assert REGISTRY_BY_KEY["delivery_mode"]["allowed_renderers"] == ["select"]
    assert REGISTRY_BY_KEY["delivery_mode"]["default_renderer"] == "select"
    assert REGISTRY_BY_KEY["location_id"]["allowed_renderers"] == ["select"]


def test_get_field_registry_returns_a_fresh_copy_each_call():
    """Deep-copy semantics must still hold — mutating one call's result must
    not leak into the module-level registry or the next call.

    Note: 'XXX' is deliberately avoided as the mutation sentinel — it's a
    genuine reserved ISO-4217 code ('no currency involved'), already present
    in the real data, so it can't be used to detect a leak."""
    reg1 = get_field_registry()
    entry = next(e for e in reg1 if e["key"] == "currency")
    entry["options"].append({"value": "NOT_A_REAL_CODE", "label": "mutated", "position": 999})

    reg2 = get_field_registry()
    entry2 = next(e for e in reg2 if e["key"] == "currency")
    assert not any(o["value"] == "NOT_A_REAL_CODE" for o in entry2["options"])
