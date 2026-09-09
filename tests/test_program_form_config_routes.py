"""Smoke tests for Program form configuration wiring (no DB required)."""

from app.main import app
from app.services.program_form_registry import (
    DEFAULT_CONFIGURATION_ID,
    DEFAULT_VERSION_ID,
    build_seed_sections,
)


def test_program_form_routes_registered():
    paths = set(app.openapi()["paths"])
    assert "/api/v1/programs/form-configuration/active" in paths
    assert "/api/v1/programs/{program_id}/form-configuration" in paths
    assert "/api/v1/programs/form-configuration/admin/" in paths
    assert "/api/v1/programs/form-configuration/admin/{config_id}/publish" in paths
    assert "/api/v1/programs/form-configuration/admin/{config_id}/activate" in paths
    assert "/api/v1/programs/form-configuration/admin/{config_id}/deactivate" in paths
    assert "/api/v1/programs/form-configuration/admin/{config_id}/retire" in paths
    assert "/api/v1/programs/form-configuration/admin/{config_id}/assignments" in paths
    assert "/api/v1/programs/form-configuration/admin/{config_id}/audit" in paths
    assert "/api/v1/programs/form-configuration/admin/field-registry" in paths
    assert "/api/v1/admin/program-form-configurations/" in paths


def test_seed_sections_title_category_price():
    sections = build_seed_sections()
    assert len(sections) == 1
    keys = [f["core_key"] for f in sections[0]["fields"]]
    assert keys == ["title", "category", "price"]
    assert DEFAULT_CONFIGURATION_ID.endswith("021")
    assert DEFAULT_VERSION_ID.endswith("022")


def test_program_create_schema_accepts_form_fields():
    props = app.openapi()["components"]["schemas"]["ProgramCreate"]["properties"]
    assert "form_configuration_version_id" in props
    assert "custom_values" in props


def test_program_update_schema_accepts_form_fields():
    props = app.openapi()["components"]["schemas"]["ProgramUpdate"]["properties"]
    assert "form_configuration_version_id" in props
    assert "custom_values" in props


def test_training_update_schema_accepts_form_fields():
    props = app.openapi()["components"]["schemas"]["TrainingUpdate"]["properties"]
    assert "form_configuration_version_id" in props
    assert "custom_values" in props


def test_admin_list_endpoints_support_query_params():
    schema = app.openapi()
    for path in (
        "/api/v1/admin/program-form-configurations/",
        "/api/v1/admin/training-form-configurations/",
    ):
        op = schema["paths"][path]["get"]
        names = {p["name"] for p in op.get("parameters", [])}
        assert {"status", "search", "page", "page_size"} <= names
