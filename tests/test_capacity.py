from unittest.mock import patch
from uuid import uuid4


def test_get_capacity_availability_success(client):
    listing_id = str(uuid4())
    mock_response = {
        "success": True,
        "message": "Capacity availability fetched successfully",
        "data": {
            "total_capacity": 10,
            "available_capacity": 4,
            "booking_allowed": True,
            "capacity_status": "OPEN",
        },
    }

    with patch(
        "app.api.v1.endpoints.capacity.get_capacity_availability_service",
        return_value=mock_response,
    ):
        response = client.get(f"/api/v1/capacity/{listing_id}/availability")

    assert response.status_code == 200
    assert response.json()["data"]["booking_allowed"] is True
