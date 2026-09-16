"""Mutation guards use actual identity dependencies; read access stays unchanged."""
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.v1.endpoints import event_form_config_admin, training_form_config_admin, program_form_config_admin
from app.core.dependencies import get_current_user, require_form_configuration_super_admin
from app.db.database import get_db

MODULES = [event_form_config_admin, training_form_config_admin, program_form_config_admin]


@pytest.mark.parametrize('module', MODULES)
@pytest.mark.parametrize('role', ['admin', 'provider', 'super_admin'])
def test_every_form_mutation_requires_super_admin(module, role, monkeypatch):
    from app.services import super_admin_identity
    monkeypatch.setattr(super_admin_identity, 'fetch_internal_user_by_id', lambda *a, **kw: None)
    monkeypatch.setattr(super_admin_identity, 'fetch_auth_me_profile', lambda *a, **kw: None)
    app = FastAPI()
    app.include_router(module.router, prefix='/forms')
    app.dependency_overrides[get_current_user] = lambda: {'role':role, 'id':str(uuid4())}
    app.dependency_overrides[get_db] = lambda: MagicMock()
    # Execute actual route dependency chains, but stub service side effects/response schemas.
    for name in ('create_configuration_service', 'update_configuration_service', 'delete_configuration_service',
                 'publish_configuration_service', 'activate_configuration_service', 'deactivate_configuration_service',
                 'retire_configuration_service', 'put_assignments_service'):
        monkeypatch.setattr(module, name, lambda *a, **kw: JSONResponse({'ok':True}))
    config_id = str(uuid4())
    requests = [('post','/',{'name':'Test'}), ('patch',f'/{config_id}',{'name':'Changed'}),
                ('delete',f'/{config_id}',None), ('put',f'/{config_id}/assignments',{'is_global':True})]
    requests += [('post',f'/{config_id}/{action}',None) for action in ('publish','activate','deactivate','retire')]
    with TestClient(app) as client:
        for method, path, payload in requests:
            response = client.request(method, '/forms'+path, **({'json':payload} if payload is not None else {}))
            assert response.status_code == (200 if role == 'super_admin' else 403), (path, response.text)
        # Registry is a read API used by enterprise authors.
        assert client.get('/forms/field-registry').status_code == 200


def test_inactive_super_admin_rejected(monkeypatch):
    from app.services import super_admin_identity
    monkeypatch.setattr(super_admin_identity, 'fetch_internal_user_by_id', lambda *a, **kw: None)
    app = FastAPI()
    app.include_router(training_form_config_admin.router, prefix='/forms')
    app.dependency_overrides[get_current_user] = lambda: {'role':'super_admin', 'status':'disabled'}
    app.dependency_overrides[get_db] = lambda: MagicMock()
    with TestClient(app) as client:
        assert client.post('/forms/', json={'name':'No'}).status_code == 403
