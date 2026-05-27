# tests/test_capacity.py

from unittest.mock import patch
from uuid import uuid4


def test_create_capacity_success(client):
    payload = {
        "listing_id": str(uuid4()),
        "max_capacity": 100
    }

    mock_response = {
        "message": "Capacity created successfully",
        "listing_id": payload["listing_id"],
        "max_capacity": 100
    }

    with patch(
        "app.api.v1.endpoints.capacity.create_capacity_service",
        return_value=mock_response
    ):

        response = client.post(
            "/capacity/",
            json=payload
        )

    assert response.status_code == 201
    assert response.json()["message"] == "Capacity created successfully"


def test_get_capacity_success(client):
    listing_id = str(uuid4())

    mock_response = {
        "listing_id": listing_id,
        "history": []
    }

    with patch(
        "app.api.v1.endpoints.capacity.get_capacity_service",
        return_value=mock_response
    ):

        response = client.get(f"/capacity/{listing_id}")

    assert response.status_code == 200


def test_update_capacity_success(client):
    listing_id = str(uuid4())

    payload = {
        "max_capacity": 200
    }

    mock_response = {
        "message": "Capacity updated successfully"
    }

    with patch(
        "app.api.v1.endpoints.capacity.update_capacity_service",
        return_value=mock_response
    ):

        response = client.put(
            f"/capacity/{listing_id}",
            json=payload
        )

    assert response.status_code == 200


def test_get_capacity_availability_success(client):
    listing_id = str(uuid4())

    mock_response = {
        "available_slots": 20,
        "remaining_slots": 10
    }

    with patch(
        "app.api.v1.endpoints.capacity.get_capacity_availability_service",
        return_value=mock_response
    ):

        response = client.get(
            f"/capacity/{listing_id}/availability"
        )

    assert response.status_code == 200


def test_update_capacity_status_success(client):
    listing_id = str(uuid4())

    payload = {
        "capacity_status": "FULL"
    }

    mock_response = {
        "capacity_status": "FULL"
    }

    with patch(
        "app.api.v1.endpoints.capacity.update_capacity_status_service",
        return_value=mock_response
    ):

        response = client.put(
            f"/capacity/{listing_id}/status",
            json=payload
        )

    assert response.status_code == 200


def test_invalid_uuid_capacity(client):

    response = client.get("/capacity/invalid-uuid")

    assert response.status_code == 422