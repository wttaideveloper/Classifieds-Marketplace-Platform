import pytest
from fastapi.testclient import TestClient

from app.core import dependencies as core_dependencies
from app.db import database
from app.main import app


@pytest.fixture
def client():
    app.dependency_overrides[database.get_db] = lambda: None
    app.dependency_overrides[core_dependencies.get_db] = lambda: None
    app.dependency_overrides[core_dependencies.get_current_user] = lambda: {
        "id": "00000000-0000-0000-0000-000000000001",
        "email": "user@example.com",
        "role": "customer",
    }
    app.dependency_overrides[core_dependencies.get_current_admin] = lambda: {
        "id": "1",
        "email": "admin@example.com",
        "role": "admin",
    }

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
