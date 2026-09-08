from uuid import uuid4
from unittest.mock import MagicMock

from app.core.auth_context import resolve_auth_tenant_id, resolve_auth_tenant_id_with_db


def test_resolve_auth_tenant_id_from_tenant_id_claim():
    assert resolve_auth_tenant_id({"tenant_id": "2122fbf0-64cd-4e3b-8ccb-22913912f1ea"}) == "2122fbf0-64cd-4e3b-8ccb-22913912f1ea"


def test_resolve_auth_tenant_id_from_camel_case():
    assert resolve_auth_tenant_id({"tenantId": "2122fbf0-64cd-4e3b-8ccb-22913912f1ea"}) == "2122fbf0-64cd-4e3b-8ccb-22913912f1ea"


def test_resolve_auth_tenant_id_from_membership():
    user = {"membership": {"tenantId": "2122fbf0-64cd-4e3b-8ccb-22913912f1ea"}}
    assert resolve_auth_tenant_id(user) == "2122fbf0-64cd-4e3b-8ccb-22913912f1ea"


def test_resolve_auth_tenant_id_from_nested_membership_tenant():
    user = {"membership": {"tenant": {"id": "2122fbf0-64cd-4e3b-8ccb-22913912f1ea"}}}
    assert resolve_auth_tenant_id(user) == "2122fbf0-64cd-4e3b-8ccb-22913912f1ea"


def test_resolve_auth_tenant_id_with_db_from_enterprise():
    tenant_id = uuid4()
    enterprise_id = uuid4()
    enterprise = MagicMock()
    enterprise.tenant_id = tenant_id

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = enterprise

    resolved = resolve_auth_tenant_id_with_db(db, {"enterprise_id": str(enterprise_id)})
    assert resolved == str(tenant_id)


def test_payload_to_user_maps_tenant_and_membership():
    from app.core.token_auth import payload_to_user

    user = payload_to_user(
        {
            "sub": "user-1",
            "tenant_role": "tenant_admin",
            "membership": {"tenantId": "2122fbf0-64cd-4e3b-8ccb-22913912f1ea"},
        }
    )
    assert user["id"] == "user-1"
    assert user["role"] == "provider"
    assert user["tenant_id"] == "2122fbf0-64cd-4e3b-8ccb-22913912f1ea"


def test_payload_to_user_maps_nested_membership_tenant():
    from app.core.token_auth import payload_to_user

    user = payload_to_user(
        {
            "sub": "user-2",
            "tenant_role": "tenant_admin",
            "membership": {"tenant": {"id": "2122fbf0-64cd-4e3b-8ccb-22913912f1ea"}},
        }
    )
    assert user["tenant_id"] == "2122fbf0-64cd-4e3b-8ccb-22913912f1ea"


def test_resolve_enterprise_context_from_payload_enterprise_without_auth_tenant():
    from app.services.event_form_config_service import resolve_enterprise_context

    tenant_id = uuid4()
    enterprise_id = uuid4()
    enterprise = MagicMock()
    enterprise.id = enterprise_id
    enterprise.tenant_id = tenant_id
    enterprise.status = "approved"
    enterprise.is_deleted = False

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = enterprise

    resolved_enterprise, resolved_tenant = resolve_enterprise_context(
        db,
        {"role": "provider", "id": "user-1"},
        payload_enterprise_id=enterprise_id,
        payload_tenant_id=None,
    )
    assert resolved_enterprise is enterprise
    assert resolved_tenant == tenant_id


def test_resolve_enterprise_context_from_payload_tenant_without_auth_tenant(monkeypatch):
    from app.services import event_form_config_service as svc

    tenant_id = uuid4()
    enterprise_id = uuid4()
    enterprise = MagicMock()
    enterprise.id = enterprise_id
    enterprise.tenant_id = tenant_id
    enterprise.status = "approved"

    monkeypatch.setattr(svc, "_resolve_enterprise_for_tenant", lambda db, tid: enterprise)

    resolved_enterprise, resolved_tenant = svc.resolve_enterprise_context(
        MagicMock(),
        {"role": "provider", "id": "user-1"},
        payload_enterprise_id=None,
        payload_tenant_id=tenant_id,
    )
    assert resolved_enterprise is enterprise
    assert resolved_tenant == tenant_id


def test_get_active_form_without_tenant_falls_back_to_legacy(monkeypatch):
    from app.services.event_form_config_service import get_active_form_configuration_service

    legacy = (MagicMock(name="config"), MagicMock(name="version"))
    monkeypatch.setattr(
        "app.services.event_form_config_service._resolve_active_form_configuration",
        lambda db, tenant_id: legacy,
    )
    monkeypatch.setattr(
        "app.services.event_form_config_service.build_active_response",
        lambda config, version: {"configuration_id": "cfg", "version_id": "ver", "sections": []},
    )

    result = get_active_form_configuration_service(MagicMock(), {"role": "provider", "id": "user-1"})
    assert result["configuration_id"] == "cfg"
