"""403 "Catalog access denied" on GET /products/ and /services/ for a second account.

The first account was fixed by reading its role from the /auth/me profile. Another account can still end with no role
the catalog recognises (the profile says something else, has no roles, or Auth did not answer). Two tools for that:

* GET /api/v1/auth/role-diagnostics shows, for the caller's OWN login, which roles the token and the profile carry,
  the role the API ends up with and what the catalog does with it.
* CATALOG_UNRECOGNIZED_ROLE_READ_ONLY (off by default) lets such a user browse read-only like an anonymous visitor.
"""
import logging

import pytest

from app.core.config import settings
from tests.test_catalog_customer_default_role import (  # noqa: F401  (fixtures are used by name)
    AUTH_ME,
    HEADERS,
    LIST_QUERY,
    catalog,
    client,
    db_session,
    enable_profile_fallback,
    login_with,
    profile,
)

DIAG = "/api/v1/auth/role-diagnostics"

STRANGE_PROFILE = {"data": {"roles": {"tenantRole": "tenant_member", "userRole": "guest", "tenantRbacRoles": ["viewer"]}}}


def diagnose(client):
    resp = client.get(DIAG, headers=HEADERS)
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- role-diagnostics ---------------------------------------------------------------------------------------------

def test_a_role_in_the_token_is_reported_and_the_profile_is_not_asked(client, monkeypatch, profile):
    login_with(monkeypatch, role="customer")
    enable_profile_fallback(monkeypatch)

    out = diagnose(client)

    assert out["role_from_claims"] == "customer" and out["final_role"] == "customer"
    assert out["catalog_decision"] == "allowed as customer"
    assert "profile" not in out and profile["calls"] == 0


def test_a_role_from_the_profile_is_reported(client, monkeypatch, profile):
    login_with(monkeypatch)
    enable_profile_fallback(monkeypatch)

    out = diagnose(client)

    assert out["role_from_claims"] is None and out["role_from_profile"] == "customer"
    assert out["profile"]["lookup"] == "ok" and out["profile"]["has_roles_object"] is True
    assert out["profile"]["roles"]["user_role"] == "customer"
    assert out["catalog_decision"] == "allowed as customer"


def test_unrecognised_profile_roles_are_shown_so_they_can_be_added(client, monkeypatch, profile):
    profile["response"] = STRANGE_PROFILE
    login_with(monkeypatch)
    enable_profile_fallback(monkeypatch)

    out = diagnose(client)

    assert out["final_role"] is None and out["catalog_role"] is None
    assert out["profile"]["roles"] == {"tenant_role": "tenant_member", "user_role": "guest", "tenant_rbac_roles": ["viewer"]}
    assert out["catalog_decision"].startswith("DENIED")


def test_a_profile_without_a_roles_object_lists_the_keys_it_does_have(client, monkeypatch, profile):
    profile["response"] = {"data": {"id": "x", "membership": None, "groups": []}}
    login_with(monkeypatch)
    enable_profile_fallback(monkeypatch)

    out = diagnose(client)

    assert out["profile"]["lookup"] == "ok" and out["profile"]["has_roles_object"] is False
    assert out["profile"]["top_level_keys"] == ["data"]
    assert out["catalog_decision"].startswith("DENIED")


def test_auth_not_answering_is_reported_as_a_failed_lookup(client, monkeypatch, profile):
    profile["response"] = None
    login_with(monkeypatch)
    enable_profile_fallback(monkeypatch)

    out = diagnose(client)

    assert out["profile"]["lookup"] == "failed" and out["catalog_decision"].startswith("DENIED")


def test_no_email_or_token_is_ever_returned(client, monkeypatch, profile):
    login_with(monkeypatch, email="someone@example.com")
    enable_profile_fallback(monkeypatch)

    body = client.get(DIAG, headers=HEADERS).text

    assert "someone@example.com" not in body and "jaweb44639@aminavin.com" not in body
    assert "fake.keycloak.token" not in body


def test_it_needs_a_login(client):
    assert client.get(DIAG).status_code == 401


# --- the unresolved role is now visible in the logs ---------------------------------------------------------------

def test_an_unresolved_role_logs_what_the_profile_said(client, catalog, monkeypatch, profile, caplog):
    profile["response"] = STRANGE_PROFILE
    login_with(monkeypatch)
    enable_profile_fallback(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="app.core.token_auth"):
        assert client.get("/api/v1/products/" + LIST_QUERY, headers=HEADERS).status_code == 403

    assert "Role unresolved" in caplog.text
    assert "tenant_member" in caplog.text and "guest" in caplog.text


def test_a_profile_that_gave_no_roles_logs_that_instead(client, catalog, monkeypatch, profile, caplog):
    profile["response"] = None
    login_with(monkeypatch)
    enable_profile_fallback(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="app.core.token_auth"):
        client.get("/api/v1/products/" + LIST_QUERY, headers=HEADERS)

    assert "gave no roles" in caplog.text


# --- the opt-in switch --------------------------------------------------------------------------------------------

def test_by_default_an_unrecognised_role_is_still_denied(client, catalog, monkeypatch, profile):
    profile["response"] = STRANGE_PROFILE
    login_with(monkeypatch)
    enable_profile_fallback(monkeypatch)

    assert settings.CATALOG_UNRECOGNIZED_ROLE_READ_ONLY is False
    for path in ("/api/v1/products/", "/api/v1/services/"):
        resp = client.get(path + LIST_QUERY, headers=HEADERS)
        assert resp.status_code == 403 and resp.json() == {"detail": "Catalog access denied"}


@pytest.mark.parametrize("response", [STRANGE_PROFILE, None, {"data": {"id": "x"}}])
def test_with_the_switch_on_such_a_user_browses_read_only(client, catalog, monkeypatch, profile, response):
    service, product = catalog
    monkeypatch.setattr(settings, "CATALOG_UNRECOGNIZED_ROLE_READ_ONLY", True)
    profile["response"] = response
    login_with(monkeypatch)
    enable_profile_fallback(monkeypatch)

    for path in ("/api/v1/products/", "/api/v1/services/"):
        resp = client.get(path + LIST_QUERY, headers=HEADERS)
        assert resp.status_code == 200, resp.text
        assert resp.json()["pagination"]["total"] == 1
    assert client.get(f"/api/v1/products/{product.id}", headers=HEADERS).status_code == 200
    assert client.get(f"/api/v1/services/{service.id}", headers=HEADERS).status_code == 200


def test_with_the_switch_on_such_a_user_still_cannot_write(client, catalog, monkeypatch, profile):
    service, product = catalog
    monkeypatch.setattr(settings, "CATALOG_UNRECOGNIZED_ROLE_READ_ONLY", True)
    profile["response"] = STRANGE_PROFILE
    login_with(monkeypatch)
    enable_profile_fallback(monkeypatch)

    assert client.post("/api/v1/products/", json={}, headers=HEADERS).status_code == 403
    assert client.put(f"/api/v1/products/{product.id}", json={}, headers=HEADERS).status_code == 403
    assert client.put(f"/api/v1/services/{service.id}", json={}, headers=HEADERS).status_code == 403
    assert client.delete(f"/api/v1/products/{product.id}", headers=HEADERS).status_code == 403


def test_with_the_switch_on_the_diagnostics_say_so(client, monkeypatch, profile):
    monkeypatch.setattr(settings, "CATALOG_UNRECOGNIZED_ROLE_READ_ONLY", True)
    profile["response"] = STRANGE_PROFILE
    login_with(monkeypatch)
    enable_profile_fallback(monkeypatch)

    assert "read-only public view" in diagnose(client)["catalog_decision"]


def test_the_switch_does_not_change_anyone_with_a_real_role(client, catalog, monkeypatch, profile):
    monkeypatch.setattr(settings, "CATALOG_UNRECOGNIZED_ROLE_READ_ONLY", True)
    login_with(monkeypatch, role="customer")
    enable_profile_fallback(monkeypatch)

    assert client.get("/api/v1/products/" + LIST_QUERY, headers=HEADERS).status_code == 200
    assert profile["calls"] == 0
