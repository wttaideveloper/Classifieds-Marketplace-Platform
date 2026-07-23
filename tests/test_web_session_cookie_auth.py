from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from app.core.config import settings
from app.core.dependencies import get_current_user


app = FastAPI()


@app.get("/me")
def get_me(current_user: dict = Depends(get_current_user)):
    return current_user


client = TestClient(app)


def test_http_only_web_session_cookie_authenticates_rest_request():
    token = jwt.encode(
        {"id": "550e8400-e29b-41d4-a716-446655440000", "role": "provider"},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    response = client.get("/me", cookies={settings.WEB_SESSION_COOKIE_NAME: token})

    assert response.status_code == 200
    assert response.json()["id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert response.json()["role"] == "provider"
