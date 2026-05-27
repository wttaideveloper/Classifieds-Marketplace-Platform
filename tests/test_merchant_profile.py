# tests/test_merchant_profile.py

from unittest.mock import patch
from uuid import uuid4
from io import BytesIO

def test_get_merchant_profile_success(client):
    merchant_id = str(uuid4())

    mock_response = {
        "id": merchant_id,
        "business_name": "Test Merchant"
    }

    with patch(
        "app.api.v1.endpoints.merchant_profile.get_merchant_profile_service",
        return_value=mock_response
    ):

        response = client.get(
            f"/merchant/profile?merchant_id={merchant_id}"
        )

    assert response.status_code == 200

def test_create_business_profile_success(client):
    merchant_id = str(uuid4())

    payload = {
        "business_name": "Test Business",
        "category": "Music"
    }

    mock_response = {
        "message": "Business created successfully"
    }

    with patch(
        "app.api.v1.endpoints.merchant_profile.create_business_profile_service",
        return_value=mock_response
    ):

        response = client.post(
            f"/merchant/business?merchant_id={merchant_id}",
            json=payload
        )

    assert response.status_code == 201

def test_upload_business_logo_success(client):
    merchant_id = str(uuid4())

    mock_response = {
        "message": "Logo uploaded successfully"
    }

    with patch(
        "app.api.v1.endpoints.merchant_profile.upload_business_logo_service",
        return_value=mock_response
    ):

        response = client.post(
            f"/merchant/business/logo?merchant_id={merchant_id}",
            files={
                "file": (
                    "logo.png",
                    BytesIO(b"fake-image"),
                    "image/png"
                )
            }
        )

    assert response.status_code == 200

def test_create_listing_success(client):
    merchant_id = str(uuid4())

    payload = {
        "title": "Music Event",
        "price": 500
    }

    mock_response = {
        "message": "Listing created successfully"
    }

    with patch(
        "app.api.v1.endpoints.merchant_profile.create_listing_service",
        return_value=mock_response
    ):

        response = client.post(
            f"/merchant/listings?merchant_id={merchant_id}",
            json=payload
        )

    assert response.status_code == 201

def test_get_merchant_bookings_success(client):

    mock_response = {
        "items": [],
        "total": 0
    }

    with patch(
        "app.api.v1.endpoints.merchant_profile.get_merchant_bookings_service",
        return_value=mock_response
    ):

        response = client.get(
            "/merchant/bookings?page=1&size=10"
        )

    assert response.status_code == 200

