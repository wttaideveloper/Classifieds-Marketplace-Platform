from uuid import UUID, uuid4
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.v1.endpoints.service import router


app = FastAPI()
app.include_router(router, prefix="/services")

client = TestClient(app)

_PAGINATION = {
    "total": 1,
    "page": 1,
    "page_size": 20,
    "total_pages": 1,
}


def _service_response(**overrides):
    base = {
        "id": str(uuid4()),
        "enterprise_id": str(uuid4()),
        "service_name": "Business Consulting",
        "description": "Professional consulting",
        "category": "Consulting",
        "price": 2500.00,
        "duration_minutes": 120,
        "availability_status": True,
        "status": "active",
        "currency": "USD",
        "service_description": "Professional consulting",
        "service_category": "Consulting",
        "service_price": 2500.00,
        "duration": 120,
        "service_status": True,
        "availability": [],
    }
    base.update(overrides)
    return base

@patch(
    "app.api.v1.endpoints.service.create_service_service"
)
def test_create_service(
    mock_create_service_service
):
    service_id = str(uuid4())
    enterprise_id = str(uuid4())

    mock_create_service_service.return_value = _service_response(
        id=service_id,
        enterprise_id=enterprise_id,
    )

    payload = {
        "enterprise_id": enterprise_id,
        "service_name": "Business Consulting",
        "service_description": "Professional consulting",
        "service_category": "Consulting",
        "service_price": 2500.00,
        "duration": 120,
        "availability_status": True
    }

    response = client.post(
        "/services/",
        json=payload
    )

    assert response.status_code == 201

    data = response.json()

    assert data["service_name"] == "Business Consulting"
    assert data["category"] == "Consulting"

@patch(
    "app.api.v1.endpoints.service.create_service_service"
)
def test_create_service_accepts_provider_user_id(
    mock_create_service_service
):
    service_id = str(uuid4())
    enterprise_id = str(uuid4())
    provider_user_id = str(uuid4())

    mock_create_service_service.return_value = _service_response(
        id=service_id,
        enterprise_id=enterprise_id,
        provider_user_id=provider_user_id,
    )

    payload = {
        "enterprise_id": enterprise_id,
        "service_name": "Business Consulting",
        "service_description": "Professional consulting",
        "service_category": "Consulting",
        "service_price": 2500.00,
        "duration": 120,
        "availability_status": True,
        "provider_user_id": provider_user_id,
    }

    response = client.post(
        "/services/",
        json=payload,
    )

    assert response.status_code == 201
    created_service = mock_create_service_service.call_args.args[1]
    assert created_service.provider_user_id == UUID(provider_user_id)


def test_create_service_validation_error():

    payload = {
        "service_name": "Consulting"
    }

    response = client.post(
        "/services/",
        json=payload
    )

    assert response.status_code == 422

@patch(
    "app.api.v1.endpoints.service.get_services_service"
)
def test_get_services(
    mock_get_services_service
):

    mock_get_services_service.return_value = {
        "items": [_service_response(service_name="Consulting", category="Business")],
        "pagination": _PAGINATION,
    }

    response = client.get(
        "/services/"
    )

    assert response.status_code == 200

    data = response.json()

    assert len(data["items"]) == 1
    assert data["items"][0]["service_name"] == "Consulting"

@patch(
    "app.api.v1.endpoints.service.get_services_service"
)
def test_get_services_empty(
    mock_get_services_service
):

    mock_get_services_service.return_value = {
        "items": [],
        "pagination": {**_PAGINATION, "total": 0, "total_pages": 0},
    }

    response = client.get(
        "/services/"
    )

    assert response.status_code == 200
    assert response.json()["items"] == []

@patch(
    "app.api.v1.endpoints.service.get_service_service"
)
def test_get_service(
    mock_get_service_service
):

    service_id = str(uuid4())

    mock_get_service_service.return_value = _service_response(
        id=service_id,
        service_name="Consulting",
        category="Business",
        price=1000,
        duration_minutes=60,
    )

    response = client.get(
        f"/services/{service_id}"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["id"] == service_id

@patch(
    "app.api.v1.endpoints.service.get_service_service"
)
def test_get_service_not_found(
    mock_get_service_service
):

    mock_get_service_service.side_effect = HTTPException(
        status_code=404,
        detail="Service not found"
    )

    response = client.get(
        f"/services/{uuid4()}"
    )

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Service not found"
    }

@patch(
    "app.api.v1.endpoints.service.get_service_service"
)
def test_get_service_returns_provider_user_id(
    mock_get_service_service
):
    service_id = str(uuid4())
    provider_user_id = str(uuid4())

    mock_get_service_service.return_value = _service_response(
        id=service_id,
        provider_user_id=provider_user_id,
    )

    response = client.get(
        f"/services/{service_id}"
    )

    assert response.status_code == 200
    assert response.json()["provider_user_id"] == provider_user_id


def test_get_service_invalid_uuid():

    response = client.get(
        "/services/invalid-id"
    )

    assert response.status_code == 422

@patch(
    "app.api.v1.endpoints.service.update_service_service"
)
def test_update_service(
    mock_update_service_service
):

    service_id = str(uuid4())

    mock_update_service_service.return_value = _service_response(
        id=service_id,
        service_name="Updated Consulting",
        price=2000,
        duration_minutes=90,
    )

    payload = {
        "service_name": "Updated Consulting",
        "service_price": 2000
    }

    response = client.put(
        f"/services/{service_id}",
        json=payload
    )

    assert response.status_code == 200

    data = response.json()

    assert data["service_name"] == "Updated Consulting"
    assert data["price"] == 2000

@patch(
    "app.api.v1.endpoints.service.update_service_service"
)
def test_update_service_not_found(
    mock_update_service_service
):

    mock_update_service_service.side_effect = HTTPException(
        status_code=404,
        detail="Service not found"
    )

    response = client.put(
        f"/services/{uuid4()}",
        json={
            "service_name": "Updated"
        }
    )

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Service not found"
    }

@patch(
    "app.api.v1.endpoints.service.update_service_service"
)
def test_update_service_accepts_provider_user_id(
    mock_update_service_service
):
    service_id = str(uuid4())
    provider_user_id = str(uuid4())

    mock_update_service_service.return_value = _service_response(
        id=service_id,
        provider_user_id=provider_user_id,
    )

    response = client.put(
        f"/services/{service_id}",
        json={
            "provider_user_id": provider_user_id,
        },
    )

    assert response.status_code == 200
    updated_service = mock_update_service_service.call_args.args[2]
    assert updated_service.provider_user_id == UUID(provider_user_id)


def test_update_service_invalid_uuid():

    response = client.put(
        "/services/invalid-id",
        json={
            "service_name": "Updated"
        }
    )

    assert response.status_code == 422

@patch(
    "app.api.v1.endpoints.service.delete_service_service"
)
def test_delete_service(
    mock_delete_service_service
):

    mock_delete_service_service.return_value = None

    response = client.delete(
        f"/services/{uuid4()}"
    )

    assert response.status_code == 200

    assert response.json() == {
        "message":
        "Service marked inactive successfully"
    }

@patch(
    "app.api.v1.endpoints.service.delete_service_service"
)
def test_delete_service_not_found(
    mock_delete_service_service
):

    mock_delete_service_service.side_effect = HTTPException(
        status_code=404,
        detail="Service not found"
    )

    response = client.delete(
        f"/services/{uuid4()}"
    )

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Service not found"
    }

def test_delete_service_invalid_uuid():

    response = client.delete(
        "/services/invalid-id"
    )

    assert response.status_code == 422