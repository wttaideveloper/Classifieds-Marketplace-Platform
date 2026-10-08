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


@pytest.fixture(autouse=True)
def _no_background_review_notifications(monkeypatch):
    """Review notifications are delivered from a background thread that opens its own database session and looks
    people up over the network. Tests that exercise a review route must not start it; the tests that care about
    notifications either record the dispatch or call the delivery functions directly."""
    from app.services import review_notifications

    monkeypatch.setattr(review_notifications, "_dispatch", lambda fn, *args, **kwargs: None)
