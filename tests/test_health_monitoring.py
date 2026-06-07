from uuid import uuid4
from unittest.mock import patch


def _health_response(service_name="Marketplace API Server", service_type="Server"):
    return {
        "success": True,
        "message": "System health fetched successfully",
        "data": {
            "service_name": service_name,
            "service_type": service_type,
            "health_status": "Healthy",
            "response_time_ms": 12,
            "cpu_usage": 0,
            "memory_usage": 0,
            "disk_usage": 42.5,
            "error_count": 0,
            "checked_at": None,
            "alert_status": "Not Triggered",
        },
    }


def test_get_system_health_endpoint(client):
    with patch(
        "app.api.v1.endpoints.health.get_system_health_service",
        return_value=_health_response(),
    ):
        response = client.get("/api/v1/health/system")

    assert response.status_code == 200
    assert response.json()["data"]["health_status"] == "Healthy"


def test_get_database_health_endpoint(client):
    with patch(
        "app.api.v1.endpoints.health.get_database_health_service",
        return_value=_health_response("Primary Database", "Database"),
    ):
        response = client.get("/api/v1/health/database")

    assert response.status_code == 200
    assert response.json()["data"]["service_type"] == "Database"


def test_create_alert_config_endpoint(client):
    alert_id = str(uuid4())
    mock_response = {
        "success": True,
        "message": "Alert configuration saved successfully",
        "data": {
            "alert_id": alert_id,
            "alert_name": "High CPU",
            "metric_type": "CPU",
            "threshold_value": 85.0,
            "notification_type": "Email",
            "is_active": True,
            "alert_status": "Active",
            "created_at": None,
            "updated_at": None,
        },
    }

    with patch(
        "app.api.v1.endpoints.health.create_alert_config_service",
        return_value=mock_response,
    ):
        response = client.post(
            "/api/v1/health/alerts/config",
            json={
                "alert_name": "High CPU",
                "metric_type": "CPU",
                "threshold_value": 85,
                "notification_type": "Email",
            },
        )

    assert response.status_code == 201
    assert response.json()["data"]["alert_id"] == alert_id


def test_get_api_health_endpoint(client):
    mock_response = {
        "success": True,
        "message": "API health fetched successfully",
        "data": [
            {
                "service_name": "Marketplace API",
                "health_status": "Healthy",
                "response_time_ms": 22,
                "error_count": 0,
                "checked_at": None,
                "alert_status": "Not Triggered",
            }
        ],
    }

    with patch(
        "app.api.v1.endpoints.health.get_api_health_service",
        return_value=mock_response,
    ):
        response = client.get("/api/v1/health/apis")

    assert response.status_code == 200
    assert response.json()["data"][0]["service_name"] == "Marketplace API"


def test_get_queue_health_endpoint(client):
    with patch(
        "app.api.v1.endpoints.health.get_queue_health_service",
        return_value=_health_response("Background Jobs", "Queue"),
    ):
        response = client.get("/api/v1/health/queues")

    assert response.status_code == 200
    assert response.json()["data"]["service_type"] == "Queue"


def test_get_error_summary_endpoint(client):
    mock_response = {
        "success": True,
        "message": "Error summary fetched successfully",
        "total_errors": 2,
        "data": [
            {
                "service_name": "Marketplace API",
                "severity_level": "High",
                "error_count": 2,
                "latest_error": "Unhandled exception",
            }
        ],
    }

    with patch(
        "app.api.v1.endpoints.health.get_error_summary_service",
        return_value=mock_response,
    ):
        response = client.get("/api/v1/health/errors")

    assert response.status_code == 200
    assert response.json()["total_errors"] == 2


def test_get_metrics_endpoint(client):
    mock_response = {
        "success": True,
        "message": "Metrics fetched successfully",
        "data": {
            "health_status": "Healthy",
            "response_time_ms": 18,
            "error_count": 0,
            "average_response_time_ms": 15.5,
            "cpu_usage": 0,
            "memory_usage": 0,
            "disk_usage": 42.5,
            "alert_status": "Not Triggered",
        },
    }

    with patch(
        "app.api.v1.endpoints.health.get_metrics_service",
        return_value=mock_response,
    ):
        response = client.get("/api/v1/health/metrics")

    assert response.status_code == 200
    assert response.json()["data"]["average_response_time_ms"] == 15.5


def test_alert_threshold_must_be_positive(client):
    response = client.post(
        "/api/v1/health/alerts/config",
        json={
            "alert_name": "Bad Threshold",
            "metric_type": "CPU",
            "threshold_value": 0,
            "notification_type": "Email",
        },
    )

    assert response.status_code == 422


def test_health_monitoring_paths_are_in_openapi(client):
    openapi = client.get("/openapi.json").json()

    for path in (
        "/api/v1/health/system",
        "/api/v1/health/database",
        "/api/v1/health/apis",
        "/api/v1/health/queues",
        "/api/v1/health/errors",
        "/api/v1/health/alerts/config",
        "/api/v1/health/metrics",
    ):
        assert path in openapi["paths"]
