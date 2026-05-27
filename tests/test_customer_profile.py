# tests/test_customer_profile.py

from unittest.mock import patch
from uuid import uuid4


def test_get_customer_profile_success(client):
    customer_id = str(uuid4())

    mock_response = {
        "id": customer_id,
        "name": "John"
    }

    with patch(
        "app.api.v1.endpoints.customer_profile.get_profile_service",
        return_value=mock_response
    ):

        response = client.get(
            f"/customer/profile?customer_id={customer_id}"
        )

    assert response.status_code == 200


def test_update_customer_profile_success(client):
    customer_id = str(uuid4())

    payload = {
        "name": "Updated Name"
    }

    mock_response = {
        "id": customer_id,
        "name": "Updated Name"
    }

    with patch(
        "app.api.v1.endpoints.customer_profile.update_profile_service",
        return_value=mock_response
    ):

        response = client.put(
            f"/customer/{customer_id}/profile",
            json=payload
        )

    assert response.status_code == 200


def test_get_customer_bookings_success(client):

    mock_response = {
        "items": [],
        "total": 0,
        "page": 1,
        "size": 10
    }

    with patch(
        "app.api.v1.endpoints.customer_profile.get_customer_bookings_service",
        return_value=mock_response
    ):

        response = client.get(
            "/customer/bookings?page=1&size=10"
        )

    assert response.status_code == 200


def test_get_customer_bookings_validation_error(client):

    response = client.get(
        "/customer/bookings?page=0&size=10"
    )

    assert response.status_code == 422

