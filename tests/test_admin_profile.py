from unittest.mock import patch


def test_get_admin_profile_success(client):
    mock_response = {
        "success": True,
        "data": {
            "id": 1,
            "name": "Admin User",
            "email": "admin@example.com",
        },
    }

    with patch(
        "app.api.v1.endpoints.admin_profile.get_admin_profile_service",
        return_value=mock_response,
    ):
        response = client.get("/api/v1/admin/profile?admin_id=1")

    assert response.status_code == 200
    assert response.json()["data"]["email"] == "admin@example.com"
