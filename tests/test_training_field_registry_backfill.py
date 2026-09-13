"""Reproduces the reported bug: GET /field-registry was missing ~25 core
keys, forcing the frontend to emit them as generic custom fields, which
triggered 400 "Unknown core_key" on publish and 400 "Unknown custom field"
on training create. This proves each requested key now resolves as a real
core field AND has a backing Training column (so a value submitted for it
actually persists instead of being silently dropped)."""

from app.models.training_model import Training
from app.services.training_form_registry import REGISTRY_BY_KEY, get_field_registry, validate_sections_for_publish

REQUESTED_KEYS = [
    "tags", "requirements", "start_time", "end_time", "venue", "address",
    "instructor_name", "instructor_bio", "instructor_photo", "instructor_credentials",
    "level", "language", "promo_price", "coupon_code", "requires_approval",
    "gallery_images", "documents", "promotional_video", "prerequisites",
    "release_rule", "scheduled_publication", "randomise", "is_mandatory",
    "subtitle", "faqs", "badges",
]


def test_all_requested_keys_are_registered_as_core_fields():
    missing = [k for k in REQUESTED_KEYS if k not in REGISTRY_BY_KEY]
    assert missing == [], f"Still missing from field registry: {missing}"


def test_every_registered_key_has_a_backing_training_column():
    """A core key with no matching Training column would make publish
    succeed while silently dropping any value submitted for it — worse than
    the original error. Every key must map to a real column."""
    training_columns = {c.name for c in Training.__table__.columns}
    missing_columns = [k for k in REQUESTED_KEYS if k not in training_columns]
    assert missing_columns == [], f"Registered but no backing column: {missing_columns}"


def test_get_field_registry_exposes_all_requested_keys():
    keys = {entry["key"] for entry in get_field_registry()}
    missing = [k for k in REQUESTED_KEYS if k not in keys]
    assert missing == []


def test_publish_no_longer_rejects_previously_unknown_core_keys():
    """Reproduces the exact reported failure: a form config section with a
    core field referencing 'venue' (previously unregistered) must not raise
    'Unknown core_key' on publish."""
    sections = [
        {
            "id": "section_1",
            "stable_key": "basic",
            "label": "Basic",
            "position": 1,
            "is_enabled": True,
            "fields": [
                {"id": "f1", "source": "core", "core_key": "title", "label": "Title", "renderer": "text", "is_enabled": True, "position": 1},
                {"id": "f2", "source": "core", "core_key": "category", "label": "Category", "renderer": "text", "is_enabled": True, "position": 2},
                {"id": "f3", "source": "core", "core_key": "venue", "label": "Venue", "renderer": "text", "is_enabled": True, "position": 3},
                {"id": "f4", "source": "core", "core_key": "tags", "label": "Tags", "renderer": "multi_select", "is_enabled": True, "position": 4},
                {"id": "f5", "source": "core", "core_key": "instructor_bio", "label": "Instructor Bio", "renderer": "textarea", "is_enabled": True, "position": 5},
            ],
        }
    ]
    # Must not raise
    validate_sections_for_publish(sections, scope="global")


def test_start_time_field_supports_text_and_time_renderers():
    """Explicit ask: 'ensure text is supported for times, along with time if supported'."""
    entry = REGISTRY_BY_KEY["start_time"]
    assert "text" in entry["allowed_renderers"]
    assert "time" in entry["allowed_renderers"]
