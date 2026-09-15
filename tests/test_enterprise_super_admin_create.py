"""Reproduces the reported bug: POST /api/v1/enterprises/ (and PUT/DELETE)
403'd with "Not authorized" for a Platform Super Admin session, even though
GET worked fine with the same session.

Root cause: the create/update/delete endpoints required role in
["admin", "provider"] via require_roles(...), while a Super Admin token
resolves to role == "super_admin" (set by _is_super_admin_claim() in
token_auth.py for tokens carrying isSuperAdmin=true) — a role that was
simply missing from the allowed list. GET has no role restriction at all
(just get_current_user), which is why only POST/PUT/DELETE were affected."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.main import app


def _client_as(role: str):
    app.dependency_overrides[get_current_user] = lambda: {"role": role, "id": "u1", "email": "u@example.com"}
    app.dependency_overrides[get_db] = lambda: MagicMock()
    return TestClient(app)


def teardown_function(_):
    app.dependency_overrides.clear()


@patch("app.api.v1.endpoints.enterprise.create_enterprise_service")
def test_super_admin_can_create_enterprise(mock_create):
    mock_create.return_value = {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "business_short_name": "ABC",
        "business_legal_name": "ABC Pvt Ltd",
        "business_email": "abc@gmail.com",
        "status": "draft",
    }
    client = _client_as("super_admin")

    response = client.post(
        "/api/v1/enterprises/",
        json={"business_short_name": "ABC", "business_legal_name": "ABC Pvt Ltd", "business_email": "abc@gmail.com"},
    )
    assert response.status_code == 201


@patch("app.api.v1.endpoints.enterprise.update_enterprise_service")
def test_super_admin_can_update_enterprise(mock_update):
    mock_update.return_value = {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "business_short_name": "ABC",
        "business_legal_name": "ABC Pvt Ltd",
        "business_email": "abc@gmail.com",
        "status": "draft",
    }
    client = _client_as("super_admin")

    response = client.put(
        "/api/v1/enterprises/550e8400-e29b-41d4-a716-446655440000",
        json={"business_short_name": "ABC2"},
    )
    assert response.status_code == 200


@patch("app.api.v1.endpoints.enterprise.delete_enterprise_service")
def test_super_admin_can_delete_enterprise(mock_delete):
    mock_delete.return_value = None
    client = _client_as("super_admin")

    response = client.delete("/api/v1/enterprises/550e8400-e29b-41d4-a716-446655440000")
    assert response.status_code == 200


@patch("app.api.v1.endpoints.enterprise.create_enterprise_service")
def test_provider_can_still_create_enterprise(mock_create):
    """Regression guard: the fix must not remove the existing 'provider'
    role's access (that's a different, real permission, not the bug)."""
    mock_create.return_value = {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "business_short_name": "ABC",
        "business_legal_name": "ABC Pvt Ltd",
        "business_email": "abc@gmail.com",
        "status": "draft",
    }
    client = _client_as("provider")

    response = client.post(
        "/api/v1/enterprises/",
        json={"business_short_name": "ABC", "business_legal_name": "ABC Pvt Ltd", "business_email": "abc@gmail.com"},
    )
    assert response.status_code == 201


def test_unrelated_role_still_rejected_from_creating_enterprise():
    """The fix must be precise — only admin/provider/super_admin, not a
    blanket bypass for any authenticated user."""
    client = _client_as("customer")

    response = client.post(
        "/api/v1/enterprises/",
        json={"business_short_name": "ABC", "business_legal_name": "ABC Pvt Ltd", "business_email": "abc@gmail.com"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Not authorized"
