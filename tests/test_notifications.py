from unittest.mock import patch


def test_list_notifications_success(client):
    mock_response = {
        "success": True,
        "page": 1,
        "size": 10,
        "total_elements": 0,
        "total_pages": 0,
        "unread_count": 0,
        "data": [],
    }

    with patch(
        "app.api.v1.endpoints.notifications.list_notifications_service",
        return_value=mock_response,
    ):
        response = client.get("/api/v1/notifications?page=1&size=10")

    assert response.status_code == 200
    assert response.json()["success"] is True
