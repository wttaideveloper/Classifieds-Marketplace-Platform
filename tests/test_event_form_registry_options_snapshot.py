"""Contract for wiring the Event Form registry's new value_source metadata
into the frontend builder:

1. currency/time_zone now default to renderer 'select' for NEWLY normalized
   fields (since both carry an authoritative options list) — but an
   explicitly-set renderer on an existing/historical field is NEVER
   overridden, regardless of what the registry's default_renderer says.
2. A core field with no options of its own picks up the registry's static
   options as a default — but an explicitly-supplied (possibly narrower)
   options list from the caller always wins; the registry list is a
   default, not an enforced restriction.
3. Options are captured at normalize time (creation/update and again at
   publish) and become whatever is stored in that version's sections JSONB
   from then on — nothing re-resolves them from the "live" registry later.
"""

from app.services.event_form_config_service import normalize_sections
from app.services.event_form_registry import CURRENCY_OPTIONS, REGISTRY_BY_KEY, TIME_ZONE_OPTIONS


def _section_with_field(core_key: str, **field_overrides):
    field = {"id": "f1", "source": "core", "core_key": core_key, "label": core_key, "position": 1}
    field.update(field_overrides)
    return [{"id": "s1", "label": "Section", "position": 1, "is_enabled": True, "fields": [field]}]


def test_new_currency_field_without_explicit_renderer_defaults_to_select():
    sections = _section_with_field("currency")
    out = normalize_sections(sections, assign_ids=False)
    assert out[0]["fields"][0]["renderer"] == "select"


def test_new_time_zone_field_without_explicit_renderer_defaults_to_select():
    sections = _section_with_field("time_zone")
    out = normalize_sections(sections, assign_ids=False)
    assert out[0]["fields"][0]["renderer"] == "select"


def test_historical_field_with_explicit_text_renderer_is_never_overridden():
    """The whole point of the change: a form created/published before this
    change stored renderer='text' explicitly on its currency field. Changing
    the registry's default_renderer must not silently flip it to 'select'
    on the next normalize (e.g. at publish, or the next active-form read)."""
    sections = _section_with_field("currency", renderer="text")
    out = normalize_sections(sections, assign_ids=False)
    assert out[0]["fields"][0]["renderer"] == "text"


def test_currency_field_without_options_defaults_to_full_registry_list():
    sections = _section_with_field("currency")
    out = normalize_sections(sections, assign_ids=False)
    assert out[0]["fields"][0]["options"] == CURRENCY_OPTIONS


def test_time_zone_field_without_options_defaults_to_full_registry_list():
    sections = _section_with_field("time_zone")
    out = normalize_sections(sections, assign_ids=False)
    assert out[0]["fields"][0]["options"] == TIME_ZONE_OPTIONS


def test_explicit_narrower_options_are_respected_not_overridden():
    """Super Admin can restrict a tenant's currency choices to a subset —
    registry options are a default, not an enforced/locked list."""
    custom = [{"value": "INR", "label": "INR — Indian Rupee", "position": 1}]
    sections = _section_with_field("currency", options=custom)
    out = normalize_sections(sections, assign_ids=False)
    assert out[0]["fields"][0]["options"] == custom
    assert len(out[0]["fields"][0]["options"]) == 1


def test_category_and_location_id_get_no_static_options_fallback():
    """These have value_source set (event_categories / enterprise_locations)
    but no static options in the registry — resolved live by the FE via
    source_endpoint, not embedded server-side."""
    for key in ("category", "location_id"):
        assert REGISTRY_BY_KEY[key].get("options") is None  # confirms the registry itself has none to fall back to
        sections = _section_with_field(key)
        out = normalize_sections(sections, assign_ids=False)
        assert out[0]["fields"][0]["options"] == []


def test_normalize_is_idempotent_once_options_are_captured():
    """Simulates create -> normalize (draft) -> publish -> normalize again
    (active-form read). The second pass must reproduce exactly what the
    first one captured — that's what 'snapshotted into the version' means
    in practice, since normalize_sections is what publish/runtime both call."""
    sections = _section_with_field("currency")
    first_pass = normalize_sections(sections, assign_ids=False)
    second_pass = normalize_sections(first_pass, assign_ids=False)
    assert first_pass[0]["fields"][0]["options"] == second_pass[0]["fields"][0]["options"]
    assert second_pass[0]["fields"][0]["options"] == CURRENCY_OPTIONS


def test_registry_options_mutation_after_snapshot_does_not_retroactively_change_it():
    """Proves options are copied at normalize time, not held by reference —
    a later change to the live registry list must not silently mutate an
    already-normalized (and by extension, already-published) field."""
    sections = _section_with_field("currency")
    out = normalize_sections(sections, assign_ids=False)
    snapshotted = out[0]["fields"][0]["options"]

    original_len = len(CURRENCY_OPTIONS)
    CURRENCY_OPTIONS.append({"value": "ZZZ_TEST_ONLY", "label": "not real", "position": 999})
    try:
        assert len(snapshotted) == original_len  # unaffected by the mutation above
    finally:
        CURRENCY_OPTIONS.pop()
