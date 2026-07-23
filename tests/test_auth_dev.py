from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.auth import router
from app.core.config import settings
from app.core.dependencies import get_current_user
from app.schemas.auth_schema import TEST_PROVIDER_USER_ID

app = FastAPI()
app.include_router(router, prefix="/auth")
client = TestClient(app)


def test_list_test_users():
    response = client.get("/auth/test-users")
    assert response.status_code == 200
    body = response.json()
    assert body["provider_user_id"] == TEST_PROVIDER_USER_ID
    assert body["recommended_for_admin_messages"] == "provider"
    assert any("Invigorate" in note or "auth/login" in note for note in body["notes"])


def test_auth_integration_reference():
    response = client.get("/auth/integration")
    assert response.status_code == 200
    body = response.json()
    assert body["algorithm"] == "RS256"
    assert body["audience"] == "invigorate-api"
    assert "invigorate-healthcare" in body["issuer"]
    assert body["token_response_path"] == "tokens.access_token"
    assert body["user_id_claim"] == "sub"
    assert len(body["role_mapping"]) >= 4


def test_get_dev_token():
    response = client.get("/auth/dev-token")
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["id"] == TEST_PROVIDER_USER_ID
    assert body["user"]["role"] == "provider"


def test_post_dev_token_empty_body():
    response = client.post("/auth/dev-token", json={})
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "provider"


def test_post_dev_token_no_body():
    response = client.post("/auth/dev-token")
    assert response.status_code == 200


def test_post_dev_token_custom_provider():
    response = client.post(
        "/auth/dev-token",
        json={
            "user_id": TEST_PROVIDER_USER_ID,
            "role": "provider",
            "email": "provider@test.com",
        },
    )
    assert response.status_code == 200
    assert response.json()["user"]["id"] == TEST_PROVIDER_USER_ID


def test_dev_token_works_with_chat_dependency():
    token_response = client.get("/auth/dev-token")
    token = token_response.json()["access_token"]

    from app.core.dependencies import get_current_user as resolve_user
    from fastapi.security import HTTPAuthorizationCredentials

    user = resolve_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))
    assert user["id"] == TEST_PROVIDER_USER_ID
    assert user["role"] == "provider"


def test_dev_token_blocked_when_disabled_in_production(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "ENABLE_DEV_TOKEN", False)

    response = client.get("/auth/dev-token")
    assert response.status_code == 404


def test_dev_token_allowed_in_production_when_flag_enabled(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "ENABLE_DEV_TOKEN", True)

    response = client.get("/auth/dev-token")
    assert response.status_code == 200
