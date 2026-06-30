from unittest.mock import patch


@patch("app.api.v1.endpoints.system.health_check_service")
def test_health(mock_health_check_service, client):
    mock_health_check_service.return_value = {
        "status": "healthy",
        "database": "connected",
        "message": "Application is running successfully",
    }

    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_inventory(client):
    response = client.get("/api/v1/inventory")
    assert response.status_code == 200
    assert "apis" in response.json()
