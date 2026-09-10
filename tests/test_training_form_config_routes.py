"""Smoke tests for Training form configuration wiring (no DB required)."""

from app.main import app
from app.services.training_form_registry import (
    DEFAULT_CONFIGURATION_ID,
    DEFAULT_VERSION_ID,
    build_seed_sections,
)


def test_training_form_routes_registered():
    paths = set(app.openapi()["paths"])
    assert "/api/v1/trainings/form-configuration/active" in paths
    assert "/api/v1/trainings/{training_id}/form-configuration" in paths
    assert "/api/v1/trainings/form-configuration/admin/" in paths
    assert "/api/v1/trainings/form-configuration/admin/{config_id}/publish" in paths
    assert "/api/v1/trainings/form-configuration/admin/{config_id}/activate" in paths
    assert "/api/v1/trainings/form-configuration/admin/{config_id}/deactivate" in paths
    assert "/api/v1/trainings/form-configuration/admin/{config_id}/retire" in paths
    assert "/api/v1/trainings/form-configuration/admin/{config_id}/assignments" in paths
    assert "/api/v1/admin/training-form-configurations/" in paths


def test_seed_sections_title_category_price():
    sections = build_seed_sections()
    assert len(sections) == 1
    keys = [f["core_key"] for f in sections[0]["fields"]]
    assert keys == ["title", "category", "price"]
    assert DEFAULT_CONFIGURATION_ID.endswith("011")
    assert DEFAULT_VERSION_ID.endswith("012")


def test_training_create_schema_accepts_form_fields():
    props = app.openapi()["components"]["schemas"]["TrainingCreate"]["properties"]
    assert "form_configuration_version_id" in props
    assert "custom_values" in props


def test_training_enroll_alias_registered_alongside_enrol():
    """Reported as 'missing from Swagger' — the route was always registered
    under the British spelling (/enrol), matching the rest of this domain's
    naming (TrainingEnrolment, enrolment_start, etc). Both spellings must
    resolve to the same underlying enrolment logic."""
    paths = app.openapi()["paths"]
    assert "/api/v1/trainings/{training_id}/enrol" in paths
    assert "/api/v1/trainings/{training_id}/enroll" in paths

    enrol_op = paths["/api/v1/trainings/{training_id}/enrol"]["post"]
    enroll_op = paths["/api/v1/trainings/{training_id}/enroll"]["post"]
    assert enrol_op["operationId"] != enroll_op["operationId"]
