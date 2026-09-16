"""Custom multi_select fields: renderer='multi_select' + value_type='string[]'
+ an array value must validate cleanly through the real normalize -> validate
pipeline (Form Builder save -> Event submission), matching the Form Builder's
current persistence convention."""

from app.services.event_form_config_service import normalize_sections, validate_custom_values


def _build_multi_select_field(**overrides):
    field = {
        "source": "custom",
        "label": "multi",
        "renderer": "multi_select",
        "value_type": "string[]",
        "position": 1,
        "is_enabled": True,
        "options": [
            {"value": "option_1", "label": "Option 1"},
            {"value": "option_2", "label": "Option 2"},
        ],
    }
    field.update(overrides)
    sections = [
        {
            "stable_key": "section_custom",
            "label": "Custom",
            "position": 1,
            "is_enabled": True,
            "fields": [field],
        }
    ]
    return normalize_sections(sections, assign_ids=True)


def test_normalize_sections_preserves_string_array_value_type():
    normalized = _build_multi_select_field()
    field = normalized[0]["fields"][0]
    assert field["value_type"] == "string[]"
    assert field["renderer"] == "multi_select"


def test_array_value_validates_without_error_for_string_array_field():
    normalized = _build_multi_select_field()
    field_id = normalized[0]["fields"][0]["id"]

    # Must not raise "expects string" (or any exception) — this reproduces
    # the exact reported case: renderer=multi_select, value_type=string[],
    # submitted value=["option_1","option_2"].
    result = validate_custom_values(
        [{"field_id": field_id, "value": ["option_1", "option_2"]}],
        normalized,
    )
    assert result == [{"field_id": field_id, "value": ["option_1", "option_2"]}]


def test_array_value_with_option_not_in_allowed_list_is_rejected():
    """Distinguishes the real validation error ('Invalid multi_select
    value') from the reported symptom ('expects string') — a field with a
    correctly-persisted string[] value_type never raises the latter."""
    from fastapi import HTTPException
    import pytest

    normalized = _build_multi_select_field()
    field_id = normalized[0]["fields"][0]["id"]

    with pytest.raises(HTTPException) as exc:
        validate_custom_values([{"field_id": field_id, "value": ["not_a_real_option"]}], normalized)
    assert exc.value.status_code == 400
    assert "Invalid multi_select value" in exc.value.detail
    assert "expects string" not in exc.value.detail


def test_legacy_field_with_stale_string_value_type_does_reproduce_the_reported_error():
    """Confirms the theory: a field saved BEFORE the string[] convention
    (value_type left as 'string') is exactly what produces the reported
    'expects string' message — not a code bug, a stale field definition
    that needs to be re-saved through the current Form Builder."""
    from fastapi import HTTPException
    import pytest

    normalized = _build_multi_select_field(value_type="string")
    field_id = normalized[0]["fields"][0]["id"]

    with pytest.raises(HTTPException) as exc:
        validate_custom_values([{"field_id": field_id, "value": ["option_1", "option_2"]}], normalized)
    assert exc.value.status_code == 400
    assert "expects string" in exc.value.detail
