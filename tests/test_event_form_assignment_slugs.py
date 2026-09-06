from uuid import UUID
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.services.invigorate_auth_client import resolve_tenant_ids_from_slugs


TENANT_UUID = UUID("2122fbf0-64cd-4e3b-8ccb-22913912f1ea")


@patch("app.services.invigorate_auth_client.settings")
@patch("app.services.invigorate_auth_client.list_tenants")
def test_resolve_tenant_ids_from_slugs(mock_list_tenants, mock_settings):
    mock_settings.invigorate_internal_api_configured = True
    mock_list_tenants.return_value = [
        {"id": str(TENANT_UUID), "name": "Tester Shop", "slug": "tester-shop"},
        {"id": "00000000-0000-4000-8000-000000000099", "name": "Other", "slug": "other-shop"},
    ]

    resolved = resolve_tenant_ids_from_slugs(["tester-shop"])
    assert resolved == [TENANT_UUID]


@patch("app.services.invigorate_auth_client.settings")
@patch("app.services.invigorate_auth_client.list_tenants")
def test_resolve_tenant_ids_from_slugs_unknown(mock_list_tenants, mock_settings):
    mock_settings.invigorate_internal_api_configured = True
    mock_list_tenants.return_value = [
        {"id": str(TENANT_UUID), "name": "Tester Shop", "slug": "tester-shop"},
    ]

    with pytest.raises(HTTPException) as exc:
        resolve_tenant_ids_from_slugs(["missing-shop"])
    assert exc.value.status_code == 400
    assert "missing-shop" in exc.value.detail["unknown_slugs"]


@patch("app.services.invigorate_auth_client.settings")
def test_resolve_tenant_ids_from_slugs_requires_internal_api(mock_settings):
    mock_settings.invigorate_internal_api_configured = False

    with pytest.raises(HTTPException) as exc:
        resolve_tenant_ids_from_slugs(["tester-shop"])
    assert exc.value.status_code == 503
