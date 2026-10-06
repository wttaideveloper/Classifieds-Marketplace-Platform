"""A user who has not joined any tenant: what token claims make Products/Services work?

The Auth team asked: if a customer with no tenant is sent with role "customer" instead of null, do GET
/api/v1/products/ and /api/v1/services/ work? These tests answer it with the claim shapes the real
token -> role mapping sees (JWT claims go through get_current_user for real; only signature checking and
the /auth/me id lookup are stubbed).
"""
from uuid import uuid4

import pytest

from app.models.product_model import Product
from app.models.service_model import Service
from tests.test_catalog_access_scoping import (  # noqa: F401  (fixtures are used by name)
    ENTERPRISE_ID,
    TENANT_ID,
    _mock_keycloak_request_raw_claims,
    _seed_enterprise,
    client,
    db_session,
)

HEADERS = {"Authorization": "Bearer fake.keycloak.token"}
LIST_QUERY = "?status=active&page=1&page_size=1"  # the exact calls that returned 403


@pytest.fixture
def catalog(db_session):
    _seed_enterprise(db_session)
    service = Service(
        id=uuid4(), tenant_id=TENANT_ID, enterprise_id=ENTERPRISE_ID, service_name="Yoga class",
        service_category="Wellness", service_price=10.0, duration=30, provider_user_id=uuid4(), status="active",
    )
    product = Product(
        id=uuid4(), tenant_id=TENANT_ID, enterprise_id=ENTERPRISE_ID, product_name="Yoga mat",
        product_category="Wellness", product_price=20.0, status="active",
    )
    db_session.add_all([service, product])
    db_session.commit()
    return service, product


def login_with(monkeypatch, **claims):
    """A Keycloak token carrying exactly these claims and NO tenant (the user has joined none)."""
    _mock_keycloak_request_raw_claims(
        monkeypatch, application_user_id=str(uuid4()), claims={"sub": str(uuid4()), "azp": "invigorate-api", **claims},
    )


# --- these claim shapes work -----------------------------------------------------------------

@pytest.mark.parametrize("claims", [
    {"role": "customer"},                       # what the Auth team proposes
    {"role": "Customer"},                       # case is normalised
    {"user_role": "customer"},
    {"tenant_role": "external_user"},           # existing way to say "customer"
    {"realm_access": {"roles": ["customer", "offline_access"]}},
    {"role": "customer", "tenant_role": None, "tenant_id": None},   # explicit nulls beside it
])
def test_a_customer_with_no_tenant_can_list_and_open_products_and_services(client, catalog, monkeypatch, claims):
    service, product = catalog
    login_with(monkeypatch, **claims)

    services = client.get(f"/api/v1/services/{LIST_QUERY}", headers=HEADERS)
    products = client.get(f"/api/v1/products/{LIST_QUERY}", headers=HEADERS)
    assert services.status_code == 200, services.text
    assert products.status_code == 200, products.text
    assert services.json()["pagination"]["total"] == 1 and products.json()["pagination"]["total"] == 1
    assert services.json()["items"][0]["id"] == str(service.id)  # another tenant's active listing: customers browse everything
    assert products.json()["items"][0]["id"] == str(product.id)

    assert client.get(f"/api/v1/services/{service.id}", headers=HEADERS).status_code == 200
    assert client.get(f"/api/v1/products/{product.id}", headers=HEADERS).status_code == 200


def test_a_customer_is_read_only_and_cannot_write_catalog_items(client, catalog, monkeypatch):
    service, product = catalog
    login_with(monkeypatch, role="customer")
    for url, item in ((f"/api/v1/services/{service.id}", service), (f"/api/v1/products/{product.id}", product)):
        assert client.put(url, json={}, headers=HEADERS).status_code == 403
        assert client.delete(url, headers=HEADERS).status_code == 403
    assert client.post("/api/v1/services/", json={}, headers=HEADERS).status_code == 403
    assert client.post("/api/v1/products/", json={}, headers=HEADERS).status_code == 403


# --- these still 403, so the Auth team needs to send exactly the right claim -----------------

@pytest.mark.parametrize("claims", [
    {},                                          # no role anywhere (today's null)
    {"role": None},
    {"role": ""},
    {"role": "user"},                            # not one of admin / super_admin / provider / customer
    {"role": "patient"},
    {"tenant_role": "customer"},                 # tenant_role understands external_user, not "customer"
    {"user_role": "guest"},
    {"realm_access": {"roles": ["offline_access", "uma_authorization", "default-roles-demo"]}},
])
def test_tokens_without_a_recognised_role_are_still_denied(client, catalog, monkeypatch, claims):
    login_with(monkeypatch, **claims)
    for path in ("/api/v1/services/", "/api/v1/products/"):
        resp = client.get(path + LIST_QUERY, headers=HEADERS)
        assert resp.status_code == 403 and resp.json()["detail"] == "Catalog access denied"


def test_the_same_requests_work_with_no_token_at_all(client, catalog):
    assert client.get(f"/api/v1/services/{LIST_QUERY}").status_code == 200
    assert client.get(f"/api/v1/products/{LIST_QUERY}").status_code == 200


# --- the role comes from /auth/me when the token has none -------------------------------------
# Auth added roles.userRole = "customer" to the /auth/me profile, but the access token the API receives can
# still carry no role claim (a user who joined no tenant). The profile is the fallback.

from app.services import invigorate_auth_client as auth_client  # noqa: E402

REAL_FETCH_ROLES = auth_client.fetch_application_roles  # the catalog test helper replaces it with a stub

# the response the Auth team returned, verbatim in shape
AUTH_ME = {
    "message": "Session retrieved successfully",
    "data": {
        "id": "5ec0349a-31c0-4dbb-b463-6fa6752f1abb",
        "email": "jaweb44639@aminavin.com",
        "fullName": "Gimmy",
        "membership": None,
        "roles": {
            "tenantRole": "external_user", "tenantSlug": None, "tenantName": None, "userRole": "customer",
            "tenantRbacRoles": ["customer"], "tenantPermissions": [], "knowledgeRoles": [], "canInviteUsers": False,
        },
        "userId": "5ec0349a-31c0-4dbb-b463-6fa6752f1abb",
    },
    "authenticated": True,
}


@pytest.fixture
def profile(monkeypatch):
    """Use the real profile-roles lookup, fed by a fake /auth/me. Returns the call counter."""
    auth_client._ROLES_CACHE.clear()
    state = {"calls": 0, "response": AUTH_ME}

    def fake_profile(_token):
        state["calls"] += 1
        return state["response"]

    monkeypatch.setattr(auth_client, "fetch_auth_me_profile", fake_profile)
    yield state
    auth_client._ROLES_CACHE.clear()


def enable_profile_fallback(monkeypatch):
    monkeypatch.setattr(auth_client, "fetch_application_roles", REAL_FETCH_ROLES)


def test_the_reported_user_gets_200_once_the_profile_role_is_used(client, catalog, monkeypatch, profile):
    login_with(monkeypatch)                      # access token: no role claim at all
    enable_profile_fallback(monkeypatch)
    for path in ("/api/v1/services/", "/api/v1/products/"):
        resp = client.get(path + LIST_QUERY, headers=HEADERS)
        assert resp.status_code == 200, resp.text
        assert resp.json()["pagination"]["total"] == 1


def test_the_resolved_identity_shows_the_profile_role(client, monkeypatch, profile):
    login_with(monkeypatch)
    enable_profile_fallback(monkeypatch)
    data = client.get("/api/v1/auth/session", headers=HEADERS).json()["data"]
    assert data["role"] == "customer" and data["tenant_role"] == "external_user" and data["user_role"] == "customer"


@pytest.mark.parametrize("roles", [
    {"tenantRole": "external_user"},
    {"userRole": "customer"},
    {"tenantRbacRoles": ["customer"]},
    {"tenant_role": "external_user"},            # snake_case spelling
])
def test_any_one_profile_role_field_is_enough(client, catalog, monkeypatch, profile, roles):
    profile["response"] = {"data": {"roles": roles}}
    login_with(monkeypatch)
    enable_profile_fallback(monkeypatch)
    assert client.get("/api/v1/services/" + LIST_QUERY, headers=HEADERS).status_code == 200


def test_a_role_in_the_token_wins_and_the_profile_is_not_even_asked(client, catalog, monkeypatch, profile):
    profile["response"] = {"data": {"roles": {"tenantRole": "tenant_owner", "userRole": "admin"}}}
    login_with(monkeypatch, role="customer")
    enable_profile_fallback(monkeypatch)
    assert client.get("/api/v1/services/" + LIST_QUERY, headers=HEADERS).status_code == 200
    assert profile["calls"] == 0
    # ... and it is still a read-only customer, not the admin the profile claimed
    assert client.post("/api/v1/services/", json={}, headers=HEADERS).status_code == 403


@pytest.mark.parametrize("response", [
    None,                                                          # Auth unreachable / 404
    {"data": {"id": "x"}},                                         # profile without roles
    {"data": {"roles": {}}},
    {"data": {"roles": {"userRole": "guest", "tenantRbacRoles": ["foo"], "tenantRole": "stranger"}}},
    {"data": {"roles": ["customer"]}},                             # not the object shape we understand
])
def test_no_usable_profile_role_stays_denied(client, catalog, monkeypatch, profile, response):
    profile["response"] = response
    login_with(monkeypatch)
    enable_profile_fallback(monkeypatch)
    assert client.get("/api/v1/products/" + LIST_QUERY, headers=HEADERS).status_code == 403


def test_the_profile_is_asked_once_per_token_not_once_per_request(client, catalog, monkeypatch, profile):
    login_with(monkeypatch)
    enable_profile_fallback(monkeypatch)
    for _ in range(4):
        assert client.get("/api/v1/services/" + LIST_QUERY, headers=HEADERS).status_code == 200
    assert profile["calls"] == 1
