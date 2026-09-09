import pytest
from jose import jwt

from app.core.config import settings
from app.core.token_auth import decode_access_token, payload_to_user


def test_payload_to_user_maps_invigorate_tenant_roles():
    user = payload_to_user(
        {
            "sub": "keycloak-user-id",
            "email": "staff@example.com",
            "tenant_role": "tenant_admin",
            "user_role": "contributor",
            "tenant_rbac_roles": ["contributor", "reviewer"],
        }
    )
    assert user["id"] == "keycloak-user-id"
    assert user["role"] == "provider"
    assert user["tenant_role"] == "tenant_admin"


def test_payload_to_user_maps_external_user_to_customer():
    user = payload_to_user(
        {
            "sub": "customer-id",
            "tenant_role": "external_user",
        }
    )
    assert user["role"] == "customer"


def test_payload_to_user_uses_sub_for_keycloak():
    user = payload_to_user(
        {
            "sub": "keycloak-user-id",
            "email": "user@example.com",
            "realm_access": {"roles": ["provider"]},
        }
    )
    assert user["id"] == "keycloak-user-id"
    assert user["role"] == "provider"
    assert user["email"] == "user@example.com"


def test_payload_to_user_maps_is_super_admin_claim():
    user = payload_to_user(
        {
            "sub": "platform-super-admin-id",
            "email": "superadmin@invigor8.app",
            "isSuperAdmin": True,
            "azp": "invigorate-api",
        }
    )
    assert user["id"] == "platform-super-admin-id"
    assert user["role"] == "super_admin"
    assert user["isSuperAdmin"] is True


def test_payload_to_user_is_super_admin_overrides_other_role_claims():
    """Dedicated Super Admin JWT may still carry tenant/user role claims."""
    user = payload_to_user(
        {
            "sub": "platform-super-admin-id",
            "isSuperAdmin": True,
            "role": "provider",
            "tenant_role": "tenant_admin",
            "user_role": "internal_user",
            "tenant_rbac_roles": ["contributor"],
        }
    )
    assert user["role"] == "super_admin"


def test_payload_to_user_maps_is_super_admin_string_claim():
    user = payload_to_user(
        {
            "sub": "platform-super-admin-id",
            "is_super_admin": "true",
        }
    )
    assert user["role"] == "super_admin"


def test_payload_to_user_maps_internal_user_to_provider():
    user = payload_to_user(
        {
            "sub": "4da9b695-7a70-4104-a659-0a9ce7f09d3c",
            "tenant_role": "internal_user",
            "user_role": "internal_user",
            "azp": "invigorate-api",
        }
    )
    assert user["id"] == "4da9b695-7a70-4104-a659-0a9ce7f09d3c"
    assert user["role"] == "provider"


def test_validate_keycloak_audience_accepts_azp(monkeypatch):
    from app.core.token_auth import _validate_keycloak_audience

    monkeypatch.setattr(settings, "KEYCLOAK_AUDIENCE", "invigorate-api")
    _validate_keycloak_audience(
        {
            "aud": ["realm-management", "broker", "account"],
            "azp": "invigorate-api",
        }
    )


def test_validate_keycloak_audience_accepts_aud(monkeypatch):
    from app.core.token_auth import _validate_keycloak_audience

    monkeypatch.setattr(settings, "KEYCLOAK_AUDIENCE", "invigorate-api")
    _validate_keycloak_audience({"aud": "invigorate-api"})


def test_validate_keycloak_audience_rejects_mismatch(monkeypatch):
    from jose import JWTError

    from app.core.token_auth import _validate_keycloak_audience

    monkeypatch.setattr(settings, "KEYCLOAK_AUDIENCE", "invigorate-api")
    with pytest.raises(JWTError, match="audience mismatch"):
        _validate_keycloak_audience(
            {
                "aud": ["account"],
                "azp": "other-client",
            }
        )


def test_decode_local_hs256_token():
    token = jwt.encode(
        {
            "id": "550e8400-e29b-41d4-a716-446655440020",
            "role": "provider",
            "email": "provider@test.com",
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    payload = decode_access_token(token)
    assert payload["role"] == "provider"


def test_decode_keycloak_token_uses_jwks(monkeypatch):
    monkeypatch.setattr(settings, "KEYCLOAK_ISSUER", "https://keycloak.example.com/realms/invigorate")
    monkeypatch.setattr(settings, "KEYCLOAK_AUDIENCE", "invigorate-api")

    token = "fake.keycloak.token"
    monkeypatch.setattr(
        "app.core.token_auth.jwt.get_unverified_header",
        lambda _token: {"alg": "RS256", "kid": "kid-1"},
    )
    monkeypatch.setattr(
        "app.core.token_auth._fetch_jwks",
        lambda: {"keys": [{"kid": "kid-1", "kty": "RSA", "n": "abc", "e": "AQAB"}]},
    )
    monkeypatch.setattr(
        "app.core.token_auth.jwk.construct",
        lambda _key: object(),
    )
    decode_calls: list[dict] = []

    def fake_decode(*_args, **kwargs):
        decode_calls.append(kwargs)
        return {
            "sub": "kc-user",
            "email": "user@example.com",
            "realm_access": {"roles": ["customer"]},
            "aud": ["account"],
            "azp": "invigorate-api",
        }

    monkeypatch.setattr("app.core.token_auth.jwt.decode", fake_decode)

    payload = decode_access_token(token)
    assert payload["sub"] == "kc-user"
    assert decode_calls[0]["options"] == {"verify_aud": False, "leeway": 10}


def test_decode_keycloak_token_retries_jwks_once_on_kid_miss(monkeypatch):
    """A kid the cached JWKS doesn't know about triggers one forced refresh
    before giving up — covers the Keycloak key-rotation window."""
    from app.core.token_auth import decode_access_token

    monkeypatch.setattr(settings, "KEYCLOAK_ISSUER", "https://auth.example.com/realms/demo")
    token = "fake.keycloak.token"
    monkeypatch.setattr(
        "app.core.token_auth.jwt.get_unverified_header",
        lambda _token: {"alg": "RS256", "kid": "new-kid"},
    )

    fetch_calls: list[bool] = []

    def fake_fetch(*, force_refresh=False):
        fetch_calls.append(force_refresh)
        if force_refresh:
            return {"keys": [{"kid": "new-kid", "kty": "RSA", "n": "abc", "e": "AQAB"}]}
        return {"keys": [{"kid": "old-kid", "kty": "RSA", "n": "abc", "e": "AQAB"}]}

    monkeypatch.setattr("app.core.token_auth._fetch_jwks", fake_fetch)
    monkeypatch.setattr("app.core.token_auth.jwk.construct", lambda _key: object())
    monkeypatch.setattr(
        "app.core.token_auth.jwt.decode",
        lambda *_a, **_kw: {"sub": "kc-user", "aud": ["account"], "azp": "invigorate-api"},
    )

    payload = decode_access_token(token)
    assert payload["sub"] == "kc-user"
    assert fetch_calls == [False, True]


def test_decode_keycloak_token_raises_when_kid_missing_after_refresh(monkeypatch):
    from app.core.token_auth import decode_access_token
    from jose import JWTError

    monkeypatch.setattr(settings, "KEYCLOAK_ISSUER", "https://auth.example.com/realms/demo")
    monkeypatch.setattr(
        "app.core.token_auth.jwt.get_unverified_header",
        lambda _token: {"alg": "RS256", "kid": "ghost-kid"},
    )
    monkeypatch.setattr(
        "app.core.token_auth._fetch_jwks",
        lambda **_kw: {"keys": [{"kid": "old-kid", "kty": "RSA", "n": "abc", "e": "AQAB"}]},
    )

    with pytest.raises(JWTError, match="Unable to find matching Keycloak signing key"):
        decode_access_token("fake.keycloak.token")


def test_resolve_user_from_token_or_raise_includes_reason(monkeypatch):
    """The 401 detail includes the specific validation failure, not just a
    generic checklist, so an incident like a realm/issuer mismatch is
    diagnosable from the response (and server logs) without guessing."""
    from fastapi import HTTPException

    from app.core.token_auth import resolve_user_from_token_or_raise

    monkeypatch.setattr(settings, "KEYCLOAK_ISSUER", "https://auth.example.com/realms/demo")
    monkeypatch.setattr(settings, "KEYCLOAK_AUDIENCE", "demo-api")
    monkeypatch.setattr(
        "app.core.token_auth.jwt.get_unverified_header",
        lambda _token: {"alg": "RS256", "kid": "kid-1"},
    )
    monkeypatch.setattr(
        "app.core.token_auth._fetch_jwks",
        lambda **_kw: {"keys": [{"kid": "kid-1", "kty": "RSA", "n": "abc", "e": "AQAB"}]},
    )
    monkeypatch.setattr("app.core.token_auth.jwk.construct", lambda _key: object())

    from jose import JWTError

    def fake_decode(*_a, **_kw):
        raise JWTError("Invalid issuer")

    monkeypatch.setattr("app.core.token_auth.jwt.decode", fake_decode)

    with pytest.raises(HTTPException) as exc_info:
        resolve_user_from_token_or_raise("fake.keycloak.token")

    assert exc_info.value.status_code == 401
    assert "Invalid issuer" in exc_info.value.detail
