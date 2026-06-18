from uuid import uuid4
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.v1.endpoints.service import router


app = FastAPI()
app.include_router(router, prefix="/services")

client = TestClient(app)

@patch(
    "app.api.v1.endpoints.service.create_service_service"
)
def test_create_service(
    mock_create_service_service
):
    service_id = str(uuid4())
    enterprise_id = str(uuid4())

    mock_create_service_service.return_value = {
        "id": service_id,
        "enterprise_id": enterprise_id,
        "service_name": "Business Consulting",
        "service_description": "Professional consulting",
        "service_category": "Consulting",
        "service_price": 2500.00,
        "duration": 120,
        "availability_status": True,
        "service_status": True
    }

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
    assert data["service_category"] == "Consulting"

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

    mock_get_services_service.return_value = [
        {
            "id": str(uuid4()),
            "enterprise_id": str(uuid4()),
            "service_name": "Consulting",
            "service_description": "Business Consulting",
            "service_category": "Business",
            "service_price": 1000,
            "duration": 60,
            "availability_status": True,
            "service_status": True
        }
    ]

    response = client.get(
        "/services/"
    )

    assert response.status_code == 200

    data = response.json()

    assert len(data) == 1
    assert data[0]["service_name"] == "Consulting"

@patch(
    "app.api.v1.endpoints.service.get_services_service"
)
def test_get_services_empty(
    mock_get_services_service
):

    mock_get_services_service.return_value = []

    response = client.get(
        "/services/"
    )

    assert response.status_code == 200
    assert response.json() == []

@patch(
    "app.api.v1.endpoints.service.get_service_service"
)
def test_get_service(
    mock_get_service_service
):

    service_id = str(uuid4())

    mock_get_service_service.return_value = {
        "id": service_id,
        "enterprise_id": str(uuid4()),
        "service_name": "Consulting",
        "service_description": "Business Consulting",
        "service_category": "Business",
        "service_price": 1000,
        "duration": 60,
        "availability_status": True,
        "service_status": True
    }

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

    mock_update_service_service.return_value = {
        "id": service_id,
        "enterprise_id": str(uuid4()),
        "service_name": "Updated Consulting",
        "service_description": "Updated Description",
        "service_category": "Business",
        "service_price": 2000,
        "duration": 90,
        "availability_status": True,
        "service_status": True
    }

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
    assert data["service_price"] == 2000

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