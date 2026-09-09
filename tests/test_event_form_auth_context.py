from uuid import uuid4
from unittest.mock import MagicMock, patch

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


def test_resolve_auth_tenant_id_with_db_falls_back_to_invigorate_auth_me(monkeypatch):
    """Enterprise Admin WebAuth tokens often carry no tenant claim — resolve via the
    same live Invigorate /auth/me lookup /tenant/me performs, not a frontend value."""
    import app.services.invigorate_auth_client as auth_client

    monkeypatch.setattr(
        auth_client,
        "fetch_auth_me_profile",
        lambda token: {"user": {"tenant_id": "2122fbf0-64cd-4e3b-8ccb-22913912f1ea"}},
    )

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    resolved = resolve_auth_tenant_id_with_db(
        db, {"role": "admin", "id": "user-1"}, access_token="session-cookie-value"
    )
    assert resolved == "2122fbf0-64cd-4e3b-8ccb-22913912f1ea"


def test_resolve_auth_tenant_id_with_db_ignores_invigorate_without_access_token(monkeypatch):
    import app.services.invigorate_auth_client as auth_client

    called = {"count": 0}

    def _fail_if_called(token):
        called["count"] += 1
        return {"tenant_id": "2122fbf0-64cd-4e3b-8ccb-22913912f1ea"}

    monkeypatch.setattr(auth_client, "fetch_auth_me_profile", _fail_if_called)

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    resolved = resolve_auth_tenant_id_with_db(db, {"role": "admin", "id": "user-1"})
    assert resolved is None
    assert called["count"] == 0


def test_resolve_active_form_configuration_does_not_serve_inactive_legacy(monkeypatch):
    """Reproduces the reported bug: every configuration (selective, global,
    and the seeded Legacy/Default) is inactive. The resolver must not
    silently fall back to the legacy config just because it exists — it must
    report no active configuration."""
    from fastapi import HTTPException
    from app.services import event_form_config_service as svc

    db = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        if model.__name__ == "EventFormAssignment":
            q.filter.return_value.first.return_value = None
        elif model.__name__ == "EventFormConfiguration":
            # No config (global or the legacy one) satisfies is_active+published.
            q.filter.return_value.first.return_value = None
            q.filter.return_value.order_by.return_value.first.return_value = None
        return q

    db.query.side_effect = query_side_effect

    try:
        svc._resolve_active_form_configuration(db, tenant_id=None)
        assert False, "expected HTTPException(404) — resolver served the inactive legacy config"
    except HTTPException as exc:
        assert exc.status_code == 404


def test_resolve_active_form_configuration_uses_legacy_only_when_active(monkeypatch):
    """The legacy config IS still usable — but only while it's actually
    active+published, same as any other fallback candidate."""
    from app.services import event_form_config_service as svc
    from uuid import UUID as _UUID

    legacy_config = MagicMock(id=_UUID(svc.LEGACY_CONFIGURATION_ID))
    legacy_version = MagicMock()

    db = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        if model.__name__ == "EventFormAssignment":
            q.filter.return_value.first.return_value = None
        elif model.__name__ == "EventFormConfiguration":
            q.filter.return_value.first.return_value = legacy_config
            q.filter.return_value.order_by.return_value.first.return_value = None
        return q

    db.query.side_effect = query_side_effect
    monkeypatch.setattr(svc, "_get_published_version", lambda db, config_id, version_num=None: legacy_version)

    config, version = svc._resolve_active_form_configuration(db, tenant_id=None)
    assert config is legacy_config
    assert version is legacy_version


def test_get_active_form_configuration_resolves_selective_config_via_access_token(monkeypatch):
    """Reproduces the reported bug: WebAuth session has no local tenant claim, but the
    tenant resolved through the live Invigorate lookup has an active selective config."""
    from app.services import event_form_config_service as svc

    tenant_id = "2122fbf0-64cd-4e3b-8ccb-22913912f1ea"
    selective_config = MagicMock(name="selective_config")
    selective_version = MagicMock(name="selective_version")

    monkeypatch.setattr(
        svc,
        "resolve_auth_tenant_id_with_db",
        lambda db, current_user, access_token=None: tenant_id if access_token else None,
    )

    seen_tenant_ids = []

    def _fake_resolve_active(db, tenant_uuid):
        seen_tenant_ids.append(tenant_uuid)
        return (selective_config, selective_version)

    monkeypatch.setattr(svc, "_resolve_active_form_configuration", _fake_resolve_active)
    monkeypatch.setattr(
        svc,
        "build_active_response",
        lambda config, version: {"configuration_id": "selective", "scope": "selective"},
    )

    result = svc.get_active_form_configuration_service(
        MagicMock(),
        {"role": "admin", "id": "user-1"},
        access_token="session-cookie-value",
    )

    assert result == {"configuration_id": "selective", "scope": "selective"}
    assert str(seen_tenant_ids[0]) == tenant_id


def test_deactivate_configuration_service_returns_all_response_model_fields():
    """Reproduces the reported 500: the DB mutation (is_active=False) always
    succeeded, but the returned dict was missing `status`, a required field
    on ActivationResponse — FastAPI's response-model validation raised an
    uncaught error after the commit had already landed."""
    from app.schemas.event_form_config_schema import ActivationResponse
    from app.services.event_form_config_service import deactivate_configuration_service

    config_id = uuid4()
    config = MagicMock(id=config_id, status="published")
    db = MagicMock()

    with patch(
        "app.services.event_form_config_service._get_config_or_404", return_value=config
    ):
        result = deactivate_configuration_service(db, config_id, {"id": "admin-1"})

    assert config.is_active is False
    # Must not raise — this is exactly what FastAPI's response_model does on return.
    validated = ActivationResponse.model_validate(result)
    assert validated.status == "published"
    assert validated.is_active is False
