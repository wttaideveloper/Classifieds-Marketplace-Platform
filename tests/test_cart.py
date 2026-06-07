from datetime import datetime
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4


def _cart_item(customer_id=None):
    return {
        "cart_item_id": uuid4(),
        "listing_id": uuid4(),
        "quantity": 2,
        "total_price": Decimal("200.00"),
        "merchant_id": uuid4(),
    }


def test_add_to_cart_success(client):
    customer_id = str(uuid4())
    listing_id = str(uuid4())
    mock_response = {
        "success": True,
        "message": "Item added to cart",
        "data": _cart_item(customer_id),
    }

    with patch(
        "app.api.v1.endpoints.cart.add_to_cart_service",
        return_value=mock_response,
    ):
        response = client.post(
            f"/api/v1/cart?customer_id={customer_id}",
            json={"listing_id": listing_id, "quantity": 2},
        )

    assert response.status_code == 201
    assert response.json()["message"] == "Item added to cart"


def test_get_cart_success(client):
    customer_id = uuid4()
    item = _cart_item(customer_id)
    mock_response = {
        "success": True,
        "customer_id": customer_id,
        "items": [item],
        "merchants": [
            {
                "merchant_id": item["merchant_id"],
                "items": [item],
                "merchant_total": Decimal("200.00"),
            }
        ],
        "total_items": 2,
        "cart_total": Decimal("200.00"),
    }

    with patch(
        "app.api.v1.endpoints.cart.get_cart_service",
        return_value=mock_response,
    ):
        response = client.get(f"/api/v1/cart?customer_id={customer_id}")

    assert response.status_code == 200
    assert response.json()["total_items"] == 2
    assert response.json()["cart_total"] == "200.00"


def test_update_cart_item_success(client):
    customer_id = str(uuid4())
    item_id = str(uuid4())
    mock_response = {
        "success": True,
        "message": "Cart item updated",
        "data": _cart_item(customer_id),
    }

    with patch(
        "app.api.v1.endpoints.cart.update_cart_item_service",
        return_value=mock_response,
    ):
        response = client.put(
            f"/api/v1/cart/{item_id}?customer_id={customer_id}",
            json={"quantity": 3},
        )

    assert response.status_code == 200
    assert response.json()["message"] == "Cart item updated"


def test_remove_cart_item_success(client):
    customer_id = str(uuid4())
    item_id = str(uuid4())

    with patch(
        "app.api.v1.endpoints.cart.remove_cart_item_service",
        return_value={"success": True, "message": "Cart item removed"},
    ):
        response = client.delete(f"/api/v1/cart/{item_id}?customer_id={customer_id}")

    assert response.status_code == 200
    assert response.json()["message"] == "Cart item removed"


def test_clear_cart_success(client):
    customer_id = str(uuid4())

    with patch(
        "app.api.v1.endpoints.cart.clear_cart_service",
        return_value={"success": True, "message": "Cart cleared"},
    ):
        response = client.delete(f"/api/v1/cart/clear?customer_id={customer_id}")

    assert response.status_code == 200
    assert response.json()["message"] == "Cart cleared"


def test_checkout_cart_success(client):
    customer_id = uuid4()
    item = _cart_item(customer_id)
    mock_response = {
        "success": True,
        "message": "Cart validated for checkout",
        "customer_id": customer_id,
        "merchants": [
            {
                "merchant_id": item["merchant_id"],
                "items": [item],
                "merchant_total": Decimal("200.00"),
            }
        ],
        "total_items": 2,
        "cart_total": Decimal("200.00"),
        "validated_at": datetime.utcnow(),
    }

    with patch(
        "app.api.v1.endpoints.cart.checkout_cart_service",
        return_value=mock_response,
    ):
        response = client.post(f"/api/v1/cart/checkout?customer_id={customer_id}")

    assert response.status_code == 200
    assert response.json()["message"] == "Cart validated for checkout"
