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


def test_post_auth_chat_token_accepts_bearer_login_token():
    """The real POST /api/v1/auth/chat-token endpoint previously only
    accepted the HttpOnly Web session cookie (get_current_web_session_user)
    — everything else in the API already accepted Bearer via
    get_current_user. This reproduces the fix: a Bearer login token must now
    also mint a chat token, with no DB access required (pure JWT decode +
    re-sign, same as the cookie path already required)."""
    from app.main import app as real_app

    login_token = _web_session_token()
    real_client = TestClient(real_app)

    response = real_client.post(
        "/api/v1/auth/chat-token",
        headers={"Authorization": f"Bearer {login_token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert body["tenant_id"] == "550e8400-e29b-41d4-a716-446655440001"
    assert body["expires_in"] == settings.CHAT_TOKEN_EXPIRE_SECONDS

    claims = jwt.decode(body["access_token"], settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    assert claims["token_use"] == "chat"
    assert claims["sub"] == "550e8400-e29b-41d4-a716-446655440000"


def test_post_auth_chat_token_still_accepts_the_web_session_cookie():
    """Backward compatibility: the cookie path this endpoint used
    exclusively before must keep working unchanged."""
    from app.main import app as real_app

    real_client = TestClient(real_app)
    response = real_client.post(
        "/api/v1/auth/chat-token",
        cookies={settings.WEB_SESSION_COOKIE_NAME: _web_session_token()},
    )

    assert response.status_code == 200
    assert response.json()["user_id"] == "550e8400-e29b-41d4-a716-446655440000"


def test_post_auth_chat_token_401s_with_neither_bearer_nor_cookie():
    from app.main import app as real_app

    real_client = TestClient(real_app)
    response = real_client.post("/api/v1/auth/chat-token")
    assert response.status_code == 401
