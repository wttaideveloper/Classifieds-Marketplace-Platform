"""Regression coverage for Provider/Internal User catalog + conversation scoping.

Traced end-to-end (see investigation notes in the PR/commit this test lands
with): GET /api/v1/services/ resolves access via
app.core.catalog_access.get_catalog_access -> CatalogAccess, which
app.repository.service_repo.get_services applies through
app.core.catalog_access.scope_catalog_query. GET /api/v1/conversations/provider
resolves access via the authenticated user id alone
(app.services.chat_service.list_provider_conversations_service ->
app.repository.chat_repo.get_provider_conversations).

These tests exercise the real HTTP routers, the real CatalogAccess
role/tenant/provider resolution, and a real (SQLite) database seeded with
enterprise/service/conversation rows, to lock in:
  1. Provider sees a service assigned to them in their own tenant.
  2. Provider does not see another provider's service.
  3. Provider sees their assigned conversation.
  4. Provider does not see another provider's conversation.
  5. Tenant isolation is enforced even when provider_user_id matches.
  6. Admin/Super Admin see the full tenant/catalog, unaffected by the
     provider-scoping filter.
  7. Pagination totals match the number of items actually returned.
"""
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.catalog_access import get_optional_catalog_user
from app.core.config import settings
from app.core.dependencies import get_current_user
from app.db.database import Base, get_db
from app.main import app
from app.models.chat_model import Conversation, ConversationParticipant
from app.models.enterprise_model import Enterprise
from app.models.service_model import Service

PROVIDER_USER_ID = UUID("31a64f29-f2a9-42ee-814d-61af33b25e4b")
TENANT_ID = UUID("2122fbf0-64cd-4e3b-8ccb-22913912f1ea")
SERVICE_ID = UUID("71fc4238-2b94-4826-99ad-fa3e79933a77")
ENTERPRISE_ID = UUID("cd955fdf-b02c-4b83-9f10-55c722929064")
CONVERSATION_ID = UUID("31a1340b-02d6-44f2-9682-67faffa90699")


@pytest.fixture
def db_session(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    def override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    session = SessionLocal()
    yield session
    session.close()
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client(db_session):
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_optional_catalog_user, None)
    app.dependency_overrides.pop(get_current_user, None)


def _login_as(claims: dict | None):
    """Bypass JWT/bearer decoding and inject the resolved user dict directly,
    exactly as app.core.dependencies.get_current_user would hand it to
    get_catalog_access — isolates this test from auth/JWT internals we must
    not modify. Overrides both entry points: GET /services goes through
    get_optional_catalog_user, GET /conversations/provider goes through
    get_current_user directly."""
    app.dependency_overrides[get_optional_catalog_user] = lambda: claims
    if claims is not None:
        app.dependency_overrides[get_current_user] = lambda: claims


def _provider_claims(user_id=PROVIDER_USER_ID, tenant_id=TENANT_ID):
    return {
        "id": str(user_id),
        "role": "provider",
        "tenant_role": "internal_user",
        "tenant_id": str(tenant_id),
        "email": "provider@example.com",
    }


def _admin_claims(tenant_id=TENANT_ID):
    return {
        "id": str(uuid4()),
        "role": "admin",
        "tenant_role": "tenant_owner",
        "tenant_id": str(tenant_id),
        "email": "admin@example.com",
    }


def _super_admin_claims():
    return {
        "id": str(uuid4()),
        "role": "super_admin",
        "email": "super@example.com",
    }


def _seed_enterprise(db_session, enterprise_id=ENTERPRISE_ID, tenant_id=TENANT_ID):
    db_session.add(Enterprise(
        id=enterprise_id,
        tenant_id=tenant_id,
        business_short_name="Acme",
        business_legal_name="Acme Inc",
        business_email=f"{enterprise_id}@example.com",
    ))
    db_session.commit()


def _seed_service(db_session, *, service_id=None, enterprise_id=ENTERPRISE_ID, tenant_id=TENANT_ID, provider_user_id=PROVIDER_USER_ID):
    service = Service(
        id=service_id or uuid4(),
        tenant_id=tenant_id,
        enterprise_id=enterprise_id,
        service_name="Test Service",
        service_category="General",
        service_price=10.0,
        duration=30,
        provider_user_id=provider_user_id,
    )
    db_session.add(service)
    db_session.commit()
    return service


def _seed_conversation(db_session, *, conversation_id=None, tenant_id=TENANT_ID, assigned_provider_id=PROVIDER_USER_ID, participant_role="provider"):
    conversation_id = conversation_id or uuid4()
    db_session.add(Conversation(
        id=conversation_id, tenant_id=tenant_id, status="open",
        conversation_type="standard", assigned_provider_id=assigned_provider_id,
        created_by=uuid4(),
    ))
    db_session.add(ConversationParticipant(
        conversation_id=conversation_id, user_id=assigned_provider_id, role=participant_role,
    ))
    db_session.commit()
    return conversation_id


# --- 1: Provider sees a service assigned to them in their own tenant ---

def test_provider_sees_assigned_service_in_own_tenant(client, db_session):
    _seed_enterprise(db_session)
    _seed_service(db_session, service_id=SERVICE_ID)
    _login_as(_provider_claims())

    resp = client.get("/api/v1/services/")

    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"]["total"] == 1
    assert [item["id"] for item in body["items"]] == [str(SERVICE_ID)]


# --- 2: Provider cannot see another provider's service ---

def test_provider_cannot_see_another_providers_service(client, db_session):
    _seed_enterprise(db_session)
    _seed_service(db_session, provider_user_id=PROVIDER_USER_ID)
    _seed_service(db_session, provider_user_id=uuid4())  # another provider, same tenant/enterprise
    _login_as(_provider_claims())

    resp = client.get("/api/v1/services/")

    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["provider_user_id"] == str(PROVIDER_USER_ID)


# --- 3: Provider sees their assigned conversation ---

def test_provider_sees_assigned_conversation(client, db_session):
    _seed_conversation(db_session, conversation_id=CONVERSATION_ID)
    _login_as(_provider_claims())

    resp = client.get("/api/v1/conversations/provider?page=1&page_size=20")

    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["id"] == str(CONVERSATION_ID)


# --- 4: Provider cannot see another provider's conversation ---

def test_provider_cannot_see_another_providers_conversation(client, db_session):
    _seed_conversation(db_session, assigned_provider_id=PROVIDER_USER_ID)
    _seed_conversation(db_session, assigned_provider_id=uuid4())
    _login_as(_provider_claims())

    resp = client.get("/api/v1/conversations/provider?page=1&page_size=20")

    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["assigned_provider_id"] == str(PROVIDER_USER_ID)


# --- 5: Tenant isolation is enforced even when provider_user_id matches ---

def test_provider_cannot_see_service_from_another_tenant_even_with_matching_provider_id(client, db_session):
    other_tenant = uuid4()
    other_enterprise = uuid4()
    _seed_enterprise(db_session, enterprise_id=ENTERPRISE_ID, tenant_id=TENANT_ID)
    _seed_enterprise(db_session, enterprise_id=other_enterprise, tenant_id=other_tenant)
    _seed_service(db_session, enterprise_id=ENTERPRISE_ID, tenant_id=TENANT_ID, provider_user_id=PROVIDER_USER_ID)
    _seed_service(db_session, enterprise_id=other_enterprise, tenant_id=other_tenant, provider_user_id=PROVIDER_USER_ID)
    _login_as(_provider_claims(tenant_id=TENANT_ID))

    resp = client.get("/api/v1/services/")

    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["tenant_id"] == str(TENANT_ID)


# --- 6: Admin / Super Admin behavior remains unchanged ---

def test_admin_sees_all_tenant_services_regardless_of_provider(client, db_session):
    _seed_enterprise(db_session)
    _seed_service(db_session, provider_user_id=PROVIDER_USER_ID)
    _seed_service(db_session, provider_user_id=uuid4())
    _login_as(_admin_claims())

    resp = client.get("/api/v1/services/")

    assert resp.status_code == 200
    assert resp.json()["pagination"]["total"] == 2


def test_super_admin_sees_services_across_tenants(client, db_session):
    other_tenant = uuid4()
    other_enterprise = uuid4()
    _seed_enterprise(db_session, enterprise_id=ENTERPRISE_ID, tenant_id=TENANT_ID)
    _seed_enterprise(db_session, enterprise_id=other_enterprise, tenant_id=other_tenant)
    _seed_service(db_session, enterprise_id=ENTERPRISE_ID, tenant_id=TENANT_ID)
    _seed_service(db_session, enterprise_id=other_enterprise, tenant_id=other_tenant)
    _login_as(_super_admin_claims())

    resp = client.get("/api/v1/services/")

    assert resp.status_code == 200
    assert resp.json()["pagination"]["total"] == 2


# --- 7: Pagination total matches returned records ---

def test_pagination_total_matches_returned_records(client, db_session):
    _seed_enterprise(db_session)
    for _ in range(5):
        _seed_service(db_session, provider_user_id=PROVIDER_USER_ID)
    _login_as(_provider_claims())

    resp = client.get("/api/v1/services/?page=1&page_size=2")

    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"]["total"] == 5
    assert body["pagination"]["total_pages"] == 3
    assert len(body["items"]) == 2


# --- 8 & 9: identity resolution end-to-end — the actual fed037a0.../3c11fffd...
# production mismatch. Unlike _login_as() above (which injects the resolved
# user dict directly, bypassing token_auth), these two drive a real Bearer
# token through the real get_current_user -> token_auth._build_current_user
# path, with only the Keycloak JWKS/decode and the external /auth/me HTTP
# call mocked — proving Services/Conversations provider filtering actually
# scope by the application user id GET /api/v1/auth/me returns, not by the
# Keycloak `sub` claim on the access token.

def _mock_keycloak_request_identity(monkeypatch, *, keycloak_sub, application_user_id, tenant_role, tenant_id):
    monkeypatch.setattr(settings, "KEYCLOAK_ISSUER", "https://auth.example.com/realms/demo")
    monkeypatch.setattr(settings, "KEYCLOAK_AUDIENCE", "invigorate-api")
    claims = {
        "sub": keycloak_sub,
        "azp": "invigorate-api",
        "tenant_role": tenant_role,
        "tenant_id": str(tenant_id),
    }
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
    monkeypatch.setattr(
        "app.services.invigorate_auth_client.fetch_application_user_id",
        lambda _token: application_user_id,
    )


def test_provider_services_filtering_uses_auth_me_application_id(client, db_session, monkeypatch):
    """F: GET /api/v1/services/ scopes by the application user id resolved
    from /auth/me, not by the Keycloak sub on the access token — the two
    are deliberately different UUIDs here, as in the production incident."""
    _mock_keycloak_request_identity(
        monkeypatch,
        keycloak_sub=str(uuid4()),
        application_user_id=str(PROVIDER_USER_ID),
        tenant_role="internal_user",
        tenant_id=TENANT_ID,
    )
    _seed_enterprise(db_session)
    _seed_service(db_session, service_id=SERVICE_ID, provider_user_id=PROVIDER_USER_ID)
    _seed_service(db_session, provider_user_id=uuid4())  # another provider, must stay hidden

    resp = client.get("/api/v1/services/", headers={"Authorization": "Bearer fake.keycloak.token"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["id"] == str(SERVICE_ID)


def test_provider_conversations_filtering_uses_auth_me_application_id(client, db_session, monkeypatch):
    """G: GET /api/v1/conversations/provider scopes by the application user
    id resolved from /auth/me, not by the Keycloak sub on the access token."""
    _mock_keycloak_request_identity(
        monkeypatch,
        keycloak_sub=str(uuid4()),
        application_user_id=str(PROVIDER_USER_ID),
        tenant_role="internal_user",
        tenant_id=TENANT_ID,
    )
    _seed_conversation(db_session, conversation_id=CONVERSATION_ID, assigned_provider_id=PROVIDER_USER_ID)
    _seed_conversation(db_session, assigned_provider_id=uuid4())  # another provider, must stay hidden

    resp = client.get(
        "/api/v1/conversations/provider?page=1&page_size=20",
        headers={"Authorization": "Bearer fake.keycloak.token"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["id"] == str(CONVERSATION_ID)


# --- 10+: regression coverage for the Products/Services 403 investigation.
# Root cause: app.core.token_auth._map_keycloak_role fell back to an
# arbitrary entry from the token's Keycloak realm/client roles
# (next(iter(normalized), None)) whenever no recognized application role was
# found. Every Keycloak user carries infrastructure roles like
# offline_access/uma_authorization/default-roles-<realm>, so a token with no
# custom tenant_role/user_role claim resolved to one of THOSE as the
# "application role" — which then never satisfies get_catalog_access's
# admin/provider/customer/super_admin gate, producing a 403 "Catalog access
# denied" from Products/Services while GET /api/v1/enterprises/ (which only
# depends on get_current_user, with no role check at all) succeeded with the
# very same token. Fixed by returning None instead of picking arbitrarily.

def _customer_claims():
    return {
        "id": str(uuid4()),
        "role": "customer",
        "email": "customer@example.com",
    }


def _unrecognized_role_claims():
    """A token whose role/tenant_role resolve to nothing get_catalog_access
    recognizes — the general shape of the reported production bug, without
    depending on the specific Keycloak-role-fallback mechanism."""
    return {
        "id": str(uuid4()),
        "role": None,
        "email": "mystery@example.com",
    }


def _mock_keycloak_request_raw_claims(monkeypatch, *, application_user_id, claims):
    """Like _mock_keycloak_request_identity, but takes the JWT claims
    verbatim instead of assuming a tenant_role/tenant_id shape — needed to
    reproduce a token that carries ONLY generic Keycloak realm/client roles
    and no custom tenant_role/user_role/tenant_id claim at all."""
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
    monkeypatch.setattr(
        "app.services.invigorate_auth_client.fetch_application_user_id",
        lambda _token: application_user_id,
    )


# --- Test case 1 & 4 (task spec): valid application role -> allowed;
# existing customer/admin/super_admin behavior continues to work.

def test_customer_role_allowed_for_catalog_access(client, db_session):
    _seed_enterprise(db_session)
    _seed_service(db_session)
    _login_as(_customer_claims())

    resp = client.get("/api/v1/services/")

    assert resp.status_code == 200
    assert resp.json()["pagination"]["total"] == 1


# --- Test case 5: an authenticated user whose role cannot be resolved to a
# recognized application role still gets 403, with the intended reason.

def test_unrecognized_role_denied_catalog_access(client, db_session):
    _seed_enterprise(db_session)
    _seed_service(db_session)
    _login_as(_unrecognized_role_claims())

    resp = client.get("/api/v1/services/")

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Catalog access denied"


# --- Test case 2 & 6: a real Keycloak-issued token carrying only generic/
# default Keycloak roles (no application role) must be denied catalog
# access, while the SAME token continues to succeed against
# GET /api/v1/enterprises/ (no role gate there) - this is the exact
# Enterprises=200/Products&Services=403 split reported in production.

def test_generic_keycloak_roles_denied_catalog_access_but_enterprises_still_allowed(
    client, db_session, monkeypatch
):
    application_user_id = str(uuid4())
    _mock_keycloak_request_raw_claims(
        monkeypatch,
        application_user_id=application_user_id,
        claims={
            "sub": str(uuid4()),
            "azp": "invigorate-api",
            "realm_access": {
                "roles": ["offline_access", "uma_authorization", "default-roles-example"],
            },
        },
    )
    _seed_enterprise(db_session)
    _seed_service(db_session)

    headers = {"Authorization": "Bearer fake.keycloak.token"}
    services_resp = client.get("/api/v1/services/", headers=headers)
    enterprises_resp = client.get(
        "/api/v1/enterprises/?status=active&page=1&page_size=2", headers=headers
    )

    assert services_resp.status_code == 403
    assert services_resp.json()["detail"] == "Catalog access denied"
    assert enterprises_resp.status_code == 200


# --- Test case 3: a genuine application role delivered via Keycloak
# realm_access.roles (no custom tenant_role claim) is still honored.

def test_genuine_realm_role_grants_catalog_access(client, db_session, monkeypatch):
    application_user_id = str(PROVIDER_USER_ID)
    _mock_keycloak_request_raw_claims(
        monkeypatch,
        application_user_id=application_user_id,
        claims={
            "sub": str(uuid4()),
            "azp": "invigorate-api",
            "tenant_id": str(TENANT_ID),
            "realm_access": {
                "roles": ["offline_access", "provider"],
            },
        },
    )
    _seed_enterprise(db_session)
    _seed_service(db_session, provider_user_id=PROVIDER_USER_ID)

    resp = client.get("/api/v1/services/", headers={"Authorization": "Bearer fake.keycloak.token"})

    assert resp.status_code == 200
    assert resp.json()["pagination"]["total"] == 1
