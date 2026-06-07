from unittest.mock import patch
from uuid import uuid4


def test_get_admin_profile_success(client):

    admin_id = str(uuid4())

    mock_response = {
        "id": admin_id,
        "name": "Admin"
    }

    with patch(
        "app.api.v1.endpoints.admin_profile.get_admin_profile_service",
        return_value=mock_response
    ):

        response = client.get(
            f"/admin/profile?admin_id={admin_id}"
        )

    assert response.status_code == 200

def test_get_users_success(client):

    mock_response = {
        "items": [],
        "total": 0,
        "page": 1,
        "limit": 10
    }

    with patch(
        "app.api.v1.endpoints.admin_profile.admin_get_users_service",
        return_value=mock_response
    ):

        response = client.get(
            "/admin/users?page=1&limit=10"
        )

    assert response.status_code == 200

def test_update_user_status_success(client):

    user_id = str(uuid4())

    payload = {
        "status": "ACTIVE"
    }

    mock_response = {
        "message": "User status updated"
    }

    with patch(
        "app.api.v1.endpoints.admin_profile.admin_update_user_status_service",
        return_value=mock_response
    ):

        response = client.patch(
            f"/admin/users/{user_id}/status",
            json=payload
        )

    assert response.status_code == 200

def test_approve_business_success(client):

    business_id = str(uuid4())

    mock_response = {
        "message": "Business approved"
    }

    with patch(
        "app.api.v1.endpoints.admin_profile.approve_business_service",
        return_value=mock_response
    ):

        response = client.patch(
            f"/admin/businesses/{business_id}/approve"
        )

    assert response.status_code == 200

