from unittest.mock import patch
from uuid import uuid4


def test_get_merchant_profile_success(client):
    merchant_id = str(uuid4())
    mock_response = {
        "success": True,
        "data": {
            "id": merchant_id,
            "full_name": "Test Merchant",
            "business_name": "Test Business",
        },
    }

    with patch(
        "app.api.v1.endpoints.merchant_profile.get_merchant_profile_service",
        return_value=mock_response,
    ):
        response = client.get(f"/api/v1/merchant/profile?merchant_id={merchant_id}")

    assert response.status_code == 200
    assert response.json()["data"]["business_name"] == "Test Business"
