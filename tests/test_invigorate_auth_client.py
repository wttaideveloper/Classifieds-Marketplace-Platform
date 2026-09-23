from app.services.invigorate_auth_client import (
    _extract_application_user_id,
    fetch_application_user_id,
)


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._json_data


def test_fetch_application_user_id_forwards_same_bearer_token(monkeypatch):
    """The backend must call /auth/me with the SAME access token from the
    incoming request — never the application id, never a client-side cookie."""
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return _FakeResponse(200, {"id": "app-user-123"})

    monkeypatch.setattr("app.services.invigorate_auth_client.requests.get", fake_get)

    result = fetch_application_user_id("the-incoming-access-token")

    assert result == "app-user-123"
    assert captured["headers"] == {"Authorization": "Bearer the-incoming-access-token"}
    assert captured["url"].endswith("/api/v1/auth/me")


def test_fetch_application_user_id_returns_none_on_request_failure(monkeypatch):
    def fake_get(*_a, **_kw):
        raise ConnectionError("boom")

    monkeypatch.setattr("app.services.invigorate_auth_client.requests.get", fake_get)

    assert fetch_application_user_id("tok") is None


def test_fetch_application_user_id_returns_none_when_response_lacks_id(monkeypatch):
    monkeypatch.setattr(
        "app.services.invigorate_auth_client.requests.get",
        lambda *_a, **_kw: _FakeResponse(200, {"isSuperAdmin": True, "status": "active"}),
    )

    assert fetch_application_user_id("tok") is None


def test_extract_application_user_id_top_level():
    assert _extract_application_user_id({"id": "u-1"}) == "u-1"


def test_extract_application_user_id_nested_data():
    assert _extract_application_user_id({"data": {"id": "u-2"}}) == "u-2"


def test_extract_application_user_id_nested_user():
    assert _extract_application_user_id({"user": {"userId": "u-3"}}) == "u-3"


def test_extract_application_user_id_missing():
    assert _extract_application_user_id({"isSuperAdmin": True}) is None
    assert _extract_application_user_id(None) is None
