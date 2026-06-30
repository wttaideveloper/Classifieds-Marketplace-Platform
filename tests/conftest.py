from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.db.database import get_db
from app.main import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("app.main.Base.metadata.create_all", lambda *args, **kwargs: None)

    def mock_get_db():
        db = MagicMock()
        yield db

    app.dependency_overrides[get_db] = mock_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
