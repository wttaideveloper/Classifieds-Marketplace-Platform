from uuid import uuid4
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.product import router


app = FastAPI()
app.include_router(router, prefix="/products")

client = TestClient(app)

@patch("app.api.v1.endpoints.product.create_product_service")
def test_create_product(mock_create_product_service):

    product_id = str(uuid4())
    enterprise_id = str(uuid4())

    mock_create_product_service.return_value = {
        "id": product_id,
        "enterprise_id": enterprise_id,
        "product_name": "Laptop",
        "product_description": "Gaming Laptop",
        "product_category": "Electronics",
        "product_price": 50000,
        "product_images": "image.jpg",
        "product_status": True
    }

    payload = {
        "enterprise_id": enterprise_id,
        "product_name": "Laptop",
        "product_description": "Gaming Laptop",
        "product_category": "Electronics",
        "product_price": 50000,
        "product_images": "image.jpg"
    }

    response = client.post(
        "/products/",
        json=payload
    )

    assert response.status_code == 201

    data = response.json()

    assert data["product_name"] == "Laptop"
    assert data["product_category"] == "Electronics"

@patch("app.api.v1.endpoints.product.get_products_service")
def test_get_products(mock_get_products_service):

    mock_get_products_service.return_value = [
        {
            "id": str(uuid4()),
            "enterprise_id": str(uuid4()),
            "product_name": "Laptop",
            "product_description": "Gaming Laptop",
            "product_category": "Electronics",
            "product_price": 50000,
            "product_images": "image.jpg",
            "product_status": True
        }
    ]

    response = client.get("/products/")

    assert response.status_code == 200

    data = response.json()

    assert len(data) == 1
    assert data[0]["product_name"] == "Laptop"

@patch("app.api.v1.endpoints.product.get_product_service")
def test_get_product(mock_get_product_service):

    product_id = str(uuid4())

    mock_get_product_service.return_value = {
        "id": product_id,
        "enterprise_id": str(uuid4()),
        "product_name": "Laptop",
        "product_description": "Gaming Laptop",
        "product_category": "Electronics",
        "product_price": 50000,
        "product_images": "image.jpg",
        "product_status": True
    }

    response = client.get(
        f"/products/{product_id}"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["id"] == product_id

from fastapi import HTTPException


@patch("app.api.v1.endpoints.product.get_product_service")
def test_get_product_not_found(
    mock_get_product_service
):

    mock_get_product_service.side_effect = HTTPException(
        status_code=404,
        detail="Product not found"
    )

    response = client.get(
        f"/products/{uuid4()}"
    )

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Product not found"
    }

@patch("app.api.v1.endpoints.product.update_product_service")
def test_update_product(
    mock_update_product_service
):

    product_id = str(uuid4())

    mock_update_product_service.return_value = {
        "id": product_id,
        "enterprise_id": str(uuid4()),
        "product_name": "Updated Laptop",
        "product_description": "Gaming Laptop",
        "product_category": "Electronics",
        "product_price": 60000,
        "product_images": "image.jpg",
        "product_status": True
    }

    payload = {
        "product_name": "Updated Laptop",
        "product_price": 60000
    }

    response = client.put(
        f"/products/{product_id}",
        json=payload
    )

    assert response.status_code == 200

    data = response.json()

    assert data["product_name"] == "Updated Laptop"
    assert data["product_price"] == 60000

@patch("app.api.v1.endpoints.product.update_product_service")
def test_update_product_not_found(
    mock_update_product_service
):

    mock_update_product_service.side_effect = HTTPException(
        status_code=404,
        detail="Product not found"
    )

    response = client.put(
        f"/products/{uuid4()}",
        json={
            "product_name": "Updated Laptop"
        }
    )

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Product not found"
    }

@patch("app.api.v1.endpoints.product.delete_product_service")
def test_delete_product(
    mock_delete_product_service
):

    mock_delete_product_service.return_value = None

    response = client.delete(
        f"/products/{uuid4()}"
    )

    assert response.status_code == 200

    assert response.json() == {
        "message":
        "Product marked inactive successfully"
    }

@patch("app.api.v1.endpoints.product.delete_product_service")
def test_delete_product_not_found(
    mock_delete_product_service
):

    mock_delete_product_service.side_effect = HTTPException(
        status_code=404,
        detail="Product not found"
    )

    response = client.delete(
        f"/products/{uuid4()}"
    )

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Product not found"
    }

def test_create_product_validation_error():

    payload = {
        "product_name": "Laptop"
    }

    response = client.post(
        "/products/",
        json=payload
    )

    assert response.status_code == 422