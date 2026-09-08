from unittest.mock import MagicMock

from fastapi import HTTPException
import pytest

from app.core.dependencies import get_current_super_admin
from app.services.super_admin_identity import (
    profile_is_super_admin,
    profile_status_is_active,
    resolve_platform_super_admin_user,
)


def test_profile_is_super_admin_from_flag():
    assert profile_is_super_admin({"isSuperAdmin": True})
    assert profile_is_super_admin({"is_super_admin": "true"})
    assert profile_is_super_admin({"role": "super_admin"})
    assert profile_is_super_admin({"user": {"isSuperAdmin": True}})
    assert not profile_is_super_admin({"role": "provider"})


def test_profile_status_active():
    assert profile_status_is_active({"status": "active"})
    assert profile_status_is_active({})  # absent = ok
    assert not profile_status_is_active({"status": "inactive"})
    assert not profile_status_is_active({"user": {"status": "disabled"}})


def test_resolve_from_jwt_claims_without_lookup():
    user = {"id": "sa-1", "role": None, "isSuperAdmin": True}
    resolved = resolve_platform_super_admin_user(user, access_token=None)
    assert resolved is not None
    assert resolved["role"] == "super_admin"
    assert resolved["isSuperAdmin"] is True


def test_resolve_from_auth_me(monkeypatch):
    monkeypatch.setattr(
        "app.services.super_admin_identity.fetch_auth_me_profile",
        lambda _token: {"data": {"id": "sa-1", "isSuperAdmin": True, "status": "active"}},
    )
    monkeypatch.setattr(
        "app.services.super_admin_identity.fetch_internal_user_by_id",
        lambda _uid: None,
    )
    user = {"id": "sa-1", "role": "provider"}  # JWT may lack flag / map wrong
    resolved = resolve_platform_super_admin_user(user, access_token="tok")
    assert resolved["role"] == "super_admin"
    assert resolved["status"] == "active"


def test_resolve_rejects_inactive_super_admin(monkeypatch):
    monkeypatch.setattr(
        "app.services.super_admin_identity.fetch_auth_me_profile",
        lambda _token: {"isSuperAdmin": True, "status": "inactive"},
    )
    monkeypatch.setattr(
        "app.services.super_admin_identity.fetch_internal_user_by_id",
        lambda _uid: None,
    )
    assert resolve_platform_super_admin_user({"id": "sa-1"}, access_token="tok") is None


def test_get_current_super_admin_allows_resolved_identity(monkeypatch):
    request = MagicMock()
    request.headers.get.return_value = "Bearer tok-123"
    request.cookies.get.return_value = None

    monkeypatch.setattr(
        "app.services.super_admin_identity.resolve_platform_super_admin_user",
        lambda user, access_token=None: {**user, "role": "super_admin", "isSuperAdmin": True, "status": "active"},
    )

    # Call the dependency function directly (bypass Depends)
    result = get_current_super_admin(request, current_user={"id": "sa-1", "role": None})
    assert result["role"] == "super_admin"


def test_get_current_super_admin_rejects_non_admin(monkeypatch):
    request = MagicMock()
    request.headers.get.return_value = None
    request.cookies.get.return_value = None

    monkeypatch.setattr(
        "app.services.super_admin_identity.resolve_platform_super_admin_user",
        lambda user, access_token=None: None,
    )

    with pytest.raises(HTTPException) as exc:
        get_current_super_admin(request, current_user={"id": "u-1", "role": "provider"})
    assert exc.value.status_code == 403
    assert exc.value.detail == "Super Admin access required"


def test_payload_to_user_nested_user_is_super_admin():
    from app.core.token_auth import payload_to_user

    user = payload_to_user(
        {
            "sub": "sa-nested",
            "user": {"isSuperAdmin": True, "status": "active"},
        }
    )
    assert user["role"] == "super_admin"
