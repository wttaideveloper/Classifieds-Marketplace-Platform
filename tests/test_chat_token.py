from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from app.core.config import settings
from app.core.dependencies import get_current_user, get_current_web_session_user
from app.core.security import create_chat_access_token


app = FastAPI()


@app.get("/api/v1/conversations/me")
def chat_route(current_user: dict = Depends(get_current_user)):
    return current_user


@app.get("/api/v1/enterprises/me")
def non_chat_route(current_user: dict = Depends(get_current_user)):
    return current_user


@app.get("/web-session")
def web_session_route(current_user: dict = Depends(get_current_web_session_user)):
    return current_user


client = TestClient(app)


def _web_session_token() -> str:
    return jwt.encode(
        {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "role": "provider",
            "tenant_id": "550e8400-e29b-41d4-a716-446655440001",
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def test_chat_token_contains_user_tenant_scope_and_expiry():
    token = create_chat_access_token(
        {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "role": "provider",
            "tenant_id": "550e8400-e29b-41d4-a716-446655440001",
        }
    )
    claims = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])

    assert claims["token_use"] == "chat"
    assert claims["scope"] == ["chat"]
    assert claims["sub"] == "550e8400-e29b-41d4-a716-446655440000"
    assert claims["tenant_id"] == "550e8400-e29b-41d4-a716-446655440001"
    assert claims["exp"] - claims["iat"] == settings.CHAT_TOKEN_EXPIRE_SECONDS


def test_web_session_cookie_is_required_for_web_session_authentication():
    response = client.get("/web-session")
    assert response.status_code == 401

    response = client.get(
        "/web-session",
        cookies={settings.WEB_SESSION_COOKIE_NAME: _web_session_token()},
    )
    assert response.status_code == 200


def test_chat_token_is_rejected_for_non_chat_routes():
    token = create_chat_access_token(
        {"id": "550e8400-e29b-41d4-a716-446655440000", "role": "provider"}
    )
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/v1/conversations/me", headers=headers).status_code == 200
    response = client.get("/api/v1/enterprises/me", headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"] == "Chat-scoped token cannot access this endpoint"
