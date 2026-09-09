"""Smoke tests for the Courses alias — Course is the same entity as Training,
mounted at a second URL prefix. No separate model/table/service."""

from app.main import app


def test_courses_routes_alias_training():
    paths = set(app.openapi()["paths"])
    for path in (
        "/api/v1/courses/",
        "/api/v1/courses/{training_id}",
        "/api/v1/courses/{training_id}/duplicate",
        "/api/v1/courses/{training_id}/status",
        "/api/v1/courses/{training_id}/sections",
        "/api/v1/courses/{training_id}/sections/{section_id}/lessons",
        "/api/v1/courses/{training_id}/assessments",
        "/api/v1/courses/{training_id}/enrolments",
        "/api/v1/courses/{training_id}/progress",
        "/api/v1/courses/form-configuration/active",
    ):
        assert path in paths, f"missing {path}"


def test_courses_admin_approval_queue_registered():
    paths = set(app.openapi()["paths"])
    for path in (
        "/api/v1/admin/courses/pending",
        "/api/v1/admin/courses/{training_id}",
        "/api/v1/admin/courses/{training_id}/approve",
        "/api/v1/admin/courses/{training_id}/reject",
        "/api/v1/admin/courses/{training_id}/request-changes",
        "/api/v1/admin/courses/{training_id}/publish",
    ):
        assert path in paths, f"missing {path}"


def test_courses_create_shares_training_schema():
    schema = app.openapi()
    op = schema["paths"]["/api/v1/courses/"]["post"]
    ref = op["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert ref == "#/components/schemas/TrainingCreate"

    props = schema["components"]["schemas"]["TrainingCreate"]["properties"]
    assert "form_configuration_version_id" in props
    assert "custom_values" in props
