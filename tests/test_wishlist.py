# tests/test_wishlist.py

from unittest.mock import patch
from uuid import uuid4
from fastapi import HTTPException


# CREATE WISHLIST
def test_create_wishlist_success(client):

    payload = {
        "customer_id": str(uuid4()),
        "business_id": str(uuid4())
    }

    mock_response = {
        "message": "Wishlist created successfully",
        "wishlist_id": str(uuid4())
    }

    with patch(
        "app.api.v1.endpoints.wishlist.create_wishlist_service",
        return_value=mock_response
    ):

        response = client.post(
            "/wishlist/",
            json=payload
        )

    assert response.status_code == 201
    assert response.json()["message"] == "Wishlist created successfully"


# CREATE WISHLIST VALIDATION ERROR
def test_create_wishlist_validation_error(client):

    payload = {}

    response = client.post(
        "/wishlist/",
        json=payload
    )

    assert response.status_code == 422


# CREATE WISHLIST SERVICE ERROR
def test_create_wishlist_service_error(client):

    payload = {
        "customer_id": str(uuid4()),
        "business_id": str(uuid4())
    }

    with patch(
        "app.api.v1.endpoints.wishlist.create_wishlist_service",
        side_effect=HTTPException(
            status_code=400,
            detail="Wishlist already exists"
        )
    ):

        response = client.post(
            "/wishlist/",
            json=payload
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Wishlist already exists"


# GET ALL WISHLIST
def test_get_all_wishlist_success(client):

    mock_response = {
        "items": [],
        "total": 0
    }

    with patch(
        "app.api.v1.endpoints.wishlist.get_all_wishlist_service",
        return_value=mock_response
    ):

        response = client.get("/wishlist/")

    assert response.status_code == 200
    assert "items" in response.json()


# GET ALL WISHLIST SERVICE ERROR
def test_get_all_wishlist_service_error(client):

    with patch(
        "app.api.v1.endpoints.wishlist.get_all_wishlist_service",
        side_effect=HTTPException(
            status_code=404,
            detail="Wishlist not found"
        )
    ):

        response = client.get("/wishlist/")

    assert response.status_code == 404
    assert response.json()["detail"] == "Wishlist not found"


# DELETE WISHLIST
def test_delete_wishlist_success(client):

    wishlist_id = str(uuid4())

    mock_response = {
        "message": "Wishlist deleted successfully"
    }

    with patch(
        "app.api.v1.endpoints.wishlist.delete_wishlist_service",
        return_value=mock_response
    ):

        response = client.delete(
            f"/wishlist/{wishlist_id}"
        )

    assert response.status_code == 200
    assert response.json()["message"] == "Wishlist deleted successfully"


# DELETE WISHLIST INVALID UUID
def test_delete_wishlist_invalid_uuid(client):

    response = client.delete(
        "/wishlist/invalid-uuid"
    )

    assert response.status_code == 422


# DELETE WISHLIST NOT FOUND
def test_delete_wishlist_not_found(client):

    wishlist_id = str(uuid4())

    with patch(
        "app.api.v1.endpoints.wishlist.delete_wishlist_service",
        side_effect=HTTPException(
            status_code=404,
            detail="Wishlist not found"
        )
    ):

        response = client.delete(
            f"/wishlist/{wishlist_id}"
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Wishlist not found"


# GET BUSINESS WISHLIST
def test_get_business_wishlist_success(client):

    mock_response = {
        "items": [],
        "total": 0
    }

    with patch(
        "app.api.v1.endpoints.wishlist.get_business_wishlist_service",
        return_value=mock_response
    ):

        response = client.get(
            "/wishlist/businesses"
        )

    assert response.status_code == 200
    assert "items" in response.json()


# GET BUSINESS WISHLIST ERROR
def test_get_business_wishlist_error(client):

    with patch(
        "app.api.v1.endpoints.wishlist.get_business_wishlist_service",
        side_effect=HTTPException(
            status_code=404,
            detail="Business wishlist not found"
        )
    ):

        response = client.get(
            "/wishlist/businesses"
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Business wishlist not found"


# GET LISTING WISHLIST
def test_get_listing_wishlist_success(client):

    mock_response = {
        "items": [],
        "total": 0,
        "page": 1,
        "size": 10
    }

    with patch(
        "app.api.v1.endpoints.wishlist.get_listing_wishlist_service",
        return_value=mock_response
    ):

        response = client.get(
            "/wishlist/listings?page=1&size=10"
        )

    assert response.status_code == 200
    assert response.json()["page"] == 1


# GET LISTING WISHLIST VALIDATION ERROR
def test_get_listing_wishlist_validation_error(client):

    response = client.get(
        "/wishlist/listings?page=0&size=10"
    )

    assert response.status_code == 422


# GET LISTING WISHLIST SIZE LIMIT ERROR
def test_get_listing_wishlist_size_limit_error(client):

    response = client.get(
        "/wishlist/listings?page=1&size=101"
    )

    assert response.status_code == 422


# GET LISTING WISHLIST SERVICE ERROR
def test_get_listing_wishlist_service_error(client):

    with patch(
        "app.api.v1.endpoints.wishlist.get_listing_wishlist_service",
        side_effect=HTTPException(
            status_code=500,
            detail="Internal server error"
        )
    ):

        response = client.get(
            "/wishlist/listings?page=1&size=10"
        )

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"