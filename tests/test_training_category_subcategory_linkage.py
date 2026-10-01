"""Super Admin-configured Category/Subcategory options: subcategory options can
declare a parent_value (belongs to a category option), and a select field can
allow a custom ("Other") value via composite_config.frontend_settings.allow_custom_value.
Both extend the existing Training form-configuration engine — no new endpoints,
no new tables, fully backward compatible with plain free-text category/subcategory."""
from fastapi import HTTPException
import pytest

from app.services.training_form_registry import _core_field, normalize_sections
from app.services.training_form_rules import OTHER_OPTION_SENTINEL, validate_constraints, validate_category_subcategory_linkage
from app.services.training_form_config_service import validate_form_required_core_fields


def _category_field(options=None, allow_custom=False):
    field = _core_field("category", "Category", "select", 1, required=True, options=options or [
        {"value": "wellness", "label": "Wellness", "position": 1},
        {"value": "safety", "label": "Safety", "position": 2},
    ])
    if allow_custom:
        field["composite_config"] = {"frontend_settings": {"allow_custom_value": True}}
    return field


def _subcategory_field(options=None, allow_custom=False):
    field = _core_field("subcategory", "Subcategory", "select", 2, options=options or [
        {"value": "yoga", "label": "Yoga", "position": 1, "parent_value": "wellness"},
        {"value": "nutrition", "label": "Nutrition", "position": 2, "parent_value": "wellness"},
        {"value": "fire_safety", "label": "Fire Safety", "position": 1, "parent_value": "safety"},
    ])
    if allow_custom:
        field["composite_config"] = {"frontend_settings": {"allow_custom_value": True}}
    return field


def _sections(category_field, subcategory_field):
    return normalize_sections([{
        "id": "s1", "stable_key": "basic", "label": "Basic", "position": 1, "is_enabled": True,
        "fields": [category_field, subcategory_field],
    }])


# --- allow_custom_value on validate_constraints (select fields generally) ---

def test_select_rejects_value_outside_options_by_default():
    field = _category_field()
    with pytest.raises(HTTPException) as exc:
        validate_constraints(field, "not-a-real-category")
    assert exc.value.status_code == 400


def test_select_accepts_value_outside_options_when_allow_custom_value():
    field = _category_field(allow_custom=True)
    validate_constraints(field, "Custom Category Entered By Admin")  # must not raise


def test_select_still_validates_known_options_when_allow_custom_value():
    field = _category_field(allow_custom=True)
    validate_constraints(field, "wellness")  # must not raise — still a valid known option


def test_multi_select_respects_allow_custom_value():
    field = _core_field("subcategory", "Subcategory", "multi_select", 2, options=[
        {"value": "yoga", "label": "Yoga", "position": 1},
    ])
    with pytest.raises(HTTPException):
        validate_constraints(field, ["yoga", "made-up"])
    field["composite_config"] = {"frontend_settings": {"allow_custom_value": True}}
    validate_constraints(field, ["yoga", "made-up"])  # must not raise


# --- subcategory parent_value linkage ---

def test_linkage_passes_when_subcategory_belongs_to_selected_category():
    sections = _sections(_category_field(), _subcategory_field())
    sub_field = sections[0]["fields"][1]
    validate_category_subcategory_linkage({"category": "wellness", "subcategory": "yoga"}, sub_field)


def test_linkage_rejects_subcategory_from_a_different_category():
    sections = _sections(_category_field(), _subcategory_field())
    sub_field = sections[0]["fields"][1]
    with pytest.raises(HTTPException) as exc:
        validate_category_subcategory_linkage({"category": "wellness", "subcategory": "fire_safety"}, sub_field)
    assert exc.value.status_code == 400
    assert "does not belong to category" in exc.value.detail


def test_linkage_skips_custom_subcategory_value_not_in_options():
    sections = _sections(_category_field(), _subcategory_field(allow_custom=True))
    sub_field = sections[0]["fields"][1]
    # "Other" custom entry — not in options at all, so not tied to any category.
    validate_category_subcategory_linkage({"category": "wellness", "subcategory": "Post-natal recovery"}, sub_field)


def test_linkage_is_a_noop_when_no_option_declares_parent_value():
    sections = _sections(_category_field(), _subcategory_field(options=[
        {"value": "yoga", "label": "Yoga", "position": 1},  # no parent_value — legacy/unlinked config
    ]))
    sub_field = sections[0]["fields"][1]
    validate_category_subcategory_linkage({"category": "safety", "subcategory": "yoga"}, sub_field)


def test_linkage_is_a_noop_for_disabled_subcategory_field():
    validate_category_subcategory_linkage({"category": "wellness", "subcategory": "fire_safety"}, None)


# --- full pipeline: validate_form_required_core_fields wires it all together ---

def test_full_pipeline_accepts_matching_category_and_subcategory():
    sections = _sections(_category_field(), _subcategory_field())
    validate_form_required_core_fields({"category": "wellness", "subcategory": "nutrition"}, sections)


def test_full_pipeline_rejects_mismatched_category_and_subcategory():
    sections = _sections(_category_field(), _subcategory_field())
    with pytest.raises(HTTPException) as exc:
        validate_form_required_core_fields({"category": "safety", "subcategory": "yoga"}, sections)
    assert exc.value.status_code == 400


def test_full_pipeline_allows_custom_other_value_end_to_end():
    sections = _sections(_category_field(allow_custom=True), _subcategory_field(allow_custom=True))
    # Neither value is a known option — allowed only because allow_custom_value is set on both.
    validate_form_required_core_fields({"category": "Corporate Training", "subcategory": "Leadership"}, sections)


def test_full_pipeline_still_requires_category_even_with_allow_custom():
    sections = _sections(_category_field(allow_custom=True), _subcategory_field())
    with pytest.raises(HTTPException) as exc:
        validate_form_required_core_fields({"category": None, "subcategory": "yoga"}, sections)
    assert exc.value.status_code == 400


# --- "vice versa": predefined subcategory + custom ("Other") category ---

def test_linkage_accepts_predefined_subcategory_under_custom_category():
    """A custom (free-text, 'Other') category has no predefined subcategory
    option whose parent_value could ever match it — enforcing the pairing in
    this direction would make this combination permanently unsubmittable."""
    cat_field = _category_field(allow_custom=True)
    sub_field = _subcategory_field()
    validate_category_subcategory_linkage(
        {"category": "Corporate Offsite", "subcategory": "yoga"}, sub_field, cat_field,
    )


def test_linkage_still_rejects_predefined_subcategory_under_wrong_predefined_category():
    """The fix for the custom-category case must not weaken the original
    predefined-vs-predefined mismatch check."""
    cat_field = _category_field()
    sub_field = _subcategory_field()
    with pytest.raises(HTTPException) as exc:
        validate_category_subcategory_linkage(
            {"category": "safety", "subcategory": "yoga"}, sub_field, cat_field,
        )
    assert exc.value.status_code == 400


def test_linkage_without_category_field_arg_keeps_old_strict_behavior():
    """Backward compatibility: callers that don't pass category_field (the
    2-arg call signature) keep the original behavior."""
    sub_field = _subcategory_field()
    with pytest.raises(HTTPException):
        validate_category_subcategory_linkage({"category": "Custom Thing", "subcategory": "yoga"}, sub_field)


def test_full_pipeline_accepts_predefined_subcategory_under_custom_category():
    sections = _sections(_category_field(allow_custom=True), _subcategory_field())
    validate_form_required_core_fields({"category": "Offsite Retreat", "subcategory": "yoga"}, sections)


# --- the "Other" option sentinel must never be accepted as real data ---

def test_sentinel_rejected_for_select_field():
    field = _category_field(allow_custom=True)
    with pytest.raises(HTTPException) as exc:
        validate_constraints(field, OTHER_OPTION_SENTINEL)
    assert exc.value.status_code == 400
    assert OTHER_OPTION_SENTINEL in exc.value.detail


def test_sentinel_rejected_for_plain_text_field():
    """The guard is not select-specific — it must reject the sentinel for a
    plain text-rendered category/subcategory too."""
    field = _core_field("category", "Category", "text", 1, required=True)
    with pytest.raises(HTTPException) as exc:
        validate_constraints(field, OTHER_OPTION_SENTINEL)
    assert exc.value.status_code == 400


def test_sentinel_rejected_in_multi_select_list():
    field = _core_field("subcategory", "Subcategory", "multi_select", 2, options=[{"value": "yoga", "label": "Yoga", "position": 1}])
    field["composite_config"] = {"frontend_settings": {"allow_custom_value": True}}
    with pytest.raises(HTTPException):
        validate_constraints(field, ["yoga", OTHER_OPTION_SENTINEL])


def test_full_pipeline_rejects_sentinel_as_category():
    sections = _sections(_category_field(allow_custom=True), _subcategory_field(allow_custom=True))
    with pytest.raises(HTTPException) as exc:
        validate_form_required_core_fields({"category": OTHER_OPTION_SENTINEL, "subcategory": "yoga"}, sections)
    assert exc.value.status_code == 400


def test_full_pipeline_rejects_sentinel_as_subcategory():
    sections = _sections(_category_field(), _subcategory_field(allow_custom=True))
    with pytest.raises(HTTPException) as exc:
        validate_form_required_core_fields({"category": "wellness", "subcategory": OTHER_OPTION_SENTINEL}, sections)
    assert exc.value.status_code == 400


def test_sentinel_guard_does_not_block_normal_values():
    field = _category_field()
    validate_constraints(field, "wellness")  # sanity: unrelated values still pass


def test_backward_compatible_plain_text_fields_unaffected():
    """Legacy configs where category/subcategory stay renderer='text' (the
    registry default) must keep working exactly as before — no options, no
    linkage, any free-text string accepted."""
    from app.services.training_form_registry import build_default_sections
    sections = normalize_sections(build_default_sections())
    validate_form_required_core_fields(
        {"title": "My Training", "category": "Anything Free Text", "subcategory": "Also anything"}, sections
    )
