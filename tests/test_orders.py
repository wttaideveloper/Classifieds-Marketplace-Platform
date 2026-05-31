from unittest.mock import patch
from uuid import uuid4


def test_get_order_success(client):
    order_id = str(uuid4())
    mock_response = {
        "success": True,
        "data": {
            "order_id": order_id,
            "order_number": "ORD-001",
            "order_status": "Pending",
        },
    }

    with patch(
        "app.api.v1.endpoints.orders.get_order_details_service",
        return_value=mock_response,
    ):
        response = client.get(f"/api/v1/orders/{order_id}")

    assert response.status_code == 200
    assert response.json()["data"]["order_id"] == order_id
