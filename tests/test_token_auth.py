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


def test_payload_to_user_prefers_legacy_id_claim_when_present():
    """`payload_to_user` is the base JWT-claim mapping used directly for
    locally issued (HS256 dev/chat) marketplace tokens, where `id` and `sub`
    are always minted equal to the application user id. For a genuine
    Keycloak-issued token, `id` is normally absent and `sub` here is only a
    placeholder: the real application user id is resolved afterwards from
    Invigorate GET /api/v1/auth/me and always overrides this value — see
    token_auth._build_current_user, the Auth team's confirmed identity
    contract. `sub` is preserved separately as `keycloak_id`."""
    user = payload_to_user(
        {
            "id": "11111111-1111-1111-1111-111111111111",  # PostgreSQL/application user id
            "sub": "22222222-2222-2222-2222-222222222222",  # Keycloak user id
            "role": "provider",
            "email": "provider@example.com",
        }
    )
    assert user["id"] == "11111111-1111-1111-1111-111111111111"
    assert user["keycloak_id"] == "22222222-2222-2222-2222-222222222222"


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


def test_map_keycloak_role_ignores_generic_infrastructure_roles():
    """Regression for the Products/Services 403: a token carrying only
    Keycloak's own default/infrastructure roles (every Keycloak user gets
    these) and no recognized application role must resolve to no role at
    all, not to one of those roles picked arbitrarily. Catalog access relies
    on this — see get_catalog_access's "role not in (admin, provider)" 403,
    which must be reached deterministically here, not skipped because
    e.g. 'offline_access' got treated as the application role."""
    from app.core.token_auth import _map_keycloak_role

    payload = {
        "sub": "keycloak-user-id",
        "realm_access": {
            "roles": ["offline_access", "uma_authorization", "default-roles-example"],
        },
    }
    assert _map_keycloak_role(payload) is None

    user = payload_to_user(payload)
    assert user["role"] is None


def test_map_keycloak_role_ignores_generic_roles_in_resource_access():
    from app.core.token_auth import _map_keycloak_role

    payload = {
        "sub": "keycloak-user-id",
        "resource_access": {
            "invigorate-api": {"roles": ["offline_access", "uma_authorization"]},
            "account": {"roles": ["manage-account", "view-profile"]},
        },
    }
    assert _map_keycloak_role(payload) is None


def test_map_keycloak_role_still_maps_genuine_role_among_realm_roles():
    """The fix must not affect resolution when a real application role IS
    present alongside the generic Keycloak roles."""
    from app.core.token_auth import _map_keycloak_role

    payload = {
        "sub": "keycloak-user-id",
        "realm_access": {
            "roles": ["offline_access", "uma_authorization", "provider"],
        },
    }
    assert _map_keycloak_role(payload) == "provider"


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


KEYCLOAK_SUB = "fed037a0-9892-4d42-ab0c-7a798ad1fd4c"
APPLICATION_USER_ID = "3c11fffd-8022-4c3e-a7d4-ed872a8710bb"


def _mock_keycloak_decode(monkeypatch, claims: dict):
    """Make decode_access_token/_decode_access_token_with_source resolve a
    successful Keycloak (RS256) decode returning `claims`, without touching
    real JWKS/network. Mirrors the setup already used by
    test_decode_keycloak_token_uses_jwks above."""
    monkeypatch.setattr(settings, "KEYCLOAK_ISSUER", "https://auth.example.com/realms/demo")
    monkeypatch.setattr(settings, "KEYCLOAK_AUDIENCE", "invigorate-api")
    monkeypatch.setattr(
        "app.core.token_auth.jwt.get_unverified_header",
        lambda _token: {"alg": "RS256", "kid": "kid-1"},
    )
    monkeypatch.setattr(
        "app.core.token_auth._fetch_jwks",
        lambda **_kw: {"keys": [{"kid": "kid-1", "kty": "RSA", "n": "abc", "e": "AQAB"}]},
    )
    monkeypatch.setattr("app.core.token_auth.jwk.construct", lambda _key: object())
    monkeypatch.setattr("app.core.token_auth.jwt.decode", lambda *_a, **_kw: claims)


def test_current_user_id_comes_from_auth_me_not_jwt_sub(monkeypatch):
    """A: JWT `sub` is the Keycloak identity; /auth/me returns the
    application user id. current_user["id"] must be the application id and
    must NOT be the Keycloak sub — this is the Auth team's confirmed
    identity contract, and the exact fed037a0.../3c11fffd... mismatch
    reproduced in production (provider_match_count=0)."""
    from app.core.token_auth import resolve_user_from_token_or_raise

    _mock_keycloak_decode(
        monkeypatch,
        {
            "sub": KEYCLOAK_SUB,
            "azp": "invigorate-api",
            "tenant_role": "internal_user",
            "tenant_id": "tenant-1",
        },
    )
    monkeypatch.setattr(
        "app.services.invigorate_auth_client.fetch_application_user_id",
        lambda _token: APPLICATION_USER_ID,
    )

    current_user = resolve_user_from_token_or_raise("fake.keycloak.token")

    assert current_user["id"] == APPLICATION_USER_ID
    assert current_user["id"] != KEYCLOAK_SUB


def test_role_and_tenant_claims_still_populated_from_jwt(monkeypatch):
    """E: switching the identity source to /auth/me must not disturb
    role/tenant claim resolution, which stays sourced from the JWT."""
    from app.core.token_auth import resolve_user_from_token_or_raise

    _mock_keycloak_decode(
        monkeypatch,
        {
            "sub": KEYCLOAK_SUB,
            "azp": "invigorate-api",
            "tenant_role": "tenant_admin",
            "tenant_id": "tenant-42",
        },
    )
    monkeypatch.setattr(
        "app.services.invigorate_auth_client.fetch_application_user_id",
        lambda _token: APPLICATION_USER_ID,
    )

    current_user = resolve_user_from_token_or_raise("fake.keycloak.token")

    assert current_user["id"] == APPLICATION_USER_ID
    assert current_user["role"] == "provider"
    assert current_user["tenant_id"] == "tenant-42"
    assert current_user["keycloak_id"] == KEYCLOAK_SUB


def test_auth_me_failure_returns_401_not_jwt_sub_fallback(monkeypatch):
    """C: when /auth/me fails outright (network error, 5xx, etc. — all
    swallowed to None by fetch_application_user_id), authentication must
    fail with 401, and must NOT silently fall back to using JWT sub as the
    application user id."""
    from fastapi import HTTPException

    from app.core.token_auth import resolve_user_from_token_or_raise

    _mock_keycloak_decode(monkeypatch, {"sub": KEYCLOAK_SUB, "azp": "invigorate-api"})
    monkeypatch.setattr(
        "app.services.invigorate_auth_client.fetch_application_user_id",
        lambda _token: None,
    )

    with pytest.raises(HTTPException) as exc_info:
        resolve_user_from_token_or_raise("fake.keycloak.token")

    assert exc_info.value.status_code == 401


def test_auth_me_missing_application_id_fails_safely(monkeypatch):
    """D: an /auth/me response that doesn't carry an application user id
    (e.g. only isSuperAdmin/status) is treated as an identity-resolution
    failure, not a silent JWT-sub fallback."""
    from fastapi import HTTPException

    from app.core.token_auth import resolve_user_from_token_or_raise
    from app.services.invigorate_auth_client import _extract_application_user_id

    _mock_keycloak_decode(monkeypatch, {"sub": KEYCLOAK_SUB, "azp": "invigorate-api"})
    monkeypatch.setattr(
        "app.services.invigorate_auth_client.fetch_auth_me_profile",
        lambda _token: {"isSuperAdmin": True, "status": "active"},
    )
    # Sanity: the response really has no extractable application id.
    assert _extract_application_user_id({"isSuperAdmin": True, "status": "active"}) is None

    with pytest.raises(HTTPException) as exc_info:
        resolve_user_from_token_or_raise("fake.keycloak.token")

    assert exc_info.value.status_code == 401


def test_resolve_user_from_token_returns_none_when_auth_me_fails(monkeypatch):
    """Non-raising variant (used by GET /auth/session) must also refuse to
    fall back to JWT sub on an /auth/me failure."""
    from app.core.token_auth import resolve_user_from_token

    _mock_keycloak_decode(monkeypatch, {"sub": KEYCLOAK_SUB, "azp": "invigorate-api"})
    monkeypatch.setattr(
        "app.services.invigorate_auth_client.fetch_application_user_id",
        lambda _token: None,
    )

    assert resolve_user_from_token("fake.keycloak.token") is None


def test_local_dev_token_skips_auth_me_lookup(monkeypatch):
    """Locally issued (HS256) marketplace tokens — e.g. dev tokens — already
    carry the application user id directly and must NOT trigger an /auth/me
    call (Keycloak isn't even configured for these)."""
    from app.core.token_auth import resolve_user_from_token_or_raise

    def _fail_if_called(_token):
        raise AssertionError("fetch_application_user_id must not be called for local tokens")

    monkeypatch.setattr(
        "app.services.invigorate_auth_client.fetch_application_user_id",
        _fail_if_called,
    )

    token = jwt.encode(
        {"id": "550e8400-e29b-41d4-a716-446655440020", "sub": "550e8400-e29b-41d4-a716-446655440020", "role": "provider"},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    current_user = resolve_user_from_token_or_raise(token)

    assert current_user["id"] == "550e8400-e29b-41d4-a716-446655440020"
