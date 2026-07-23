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
    assert decode_calls[0]["options"] == {"verify_aud": False}
