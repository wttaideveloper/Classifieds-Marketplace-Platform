from unittest.mock import patch
from uuid import uuid4
from fastapi import status
from fastapi.testclient import TestClient
from fastapi import HTTPException
from app.main import app

client = TestClient(app)

@patch(
    "app.api.v1.endpoints.community_ecosystem.create_ecosystem_service"
)
def test_create_provider_success(mock_service):

    ecosystem_id = str(uuid4())

    mock_service.return_value = {
        "ecosystem_id": ecosystem_id,
        "provider_name": "ABC Clinic",
        "provider_type": "clinic",
        "description": "Healthcare",
        "contact_email": "abc@gmail.com",
        "contact_phone": "9876543210",
        "address": "Ahmedabad",
        "website": "https://abc.com",
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00"
    }

    payload = {
        "provider_name": "ABC Clinic",
        "provider_type": "clinic"
    }

    response = client.post("/", json=payload)

    assert response.status_code == status.HTTP_201_CREATED

    assert response.json()["provider_name"] == "ABC Clinic"

def test_create_provider_missing_name():

    payload = {
        "provider_type": "clinic"
    }

    response = client.post("/", json=payload)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

@patch(
    "app.api.v1.endpoints.community_ecosystem.get_all_ecosystem_service"
)
def test_get_all_providers(mock_service):

    mock_service.return_value = [
        {
            "ecosystem_id": str(uuid4()),
            "provider_name": "Clinic A",
            "provider_type": "clinic",
            "description": None,
            "contact_email": None,
            "contact_phone": None,
            "address": None,
            "website": None,
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00"
        }
    ]

    response = client.get("/")

    assert response.status_code == status.HTTP_200_OK

    assert len(response.json()) == 1

@patch(
    "app.api.v1.endpoints.community_ecosystem.get_by_id_ecosystem_service"
)
def test_get_provider_by_id(mock_service):

    ecosystem_id = str(uuid4())

    mock_service.return_value = {
        "ecosystem_id": ecosystem_id,
        "provider_name": "ABC Clinic",
        "provider_type": "clinic",
        "description": None,
        "contact_email": None,
        "contact_phone": None,
        "address": None,
        "website": None,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00"
    }

    response = client.get(f"/{ecosystem_id}")

    assert response.status_code == status.HTTP_200_OK

    assert response.json()["ecosystem_id"] == ecosystem_id




@patch(
    "app.api.v1.endpoints.community_ecosystem.get_by_id_ecosystem_service"
)
def test_get_provider_not_found(mock_service):

    mock_service.side_effect = HTTPException(
        status_code=404,
        detail="Provider not found"
    )

    response = client.get(f"/{uuid4()}")

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Provider not found"
    }

@patch(
    "app.api.v1.endpoints.community_ecosystem.update_ecosystem_service"
)
def test_update_provider_success(mock_service):

    ecosystem_id = str(uuid4())

    mock_service.return_value = {
        "ecosystem_id": ecosystem_id,
        "provider_name": "Updated Clinic",
        "provider_type": "clinic",
        "description": None,
        "contact_email": None,
        "contact_phone": None,
        "address": None,
        "website": None,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00"
    }

    payload = {
        "provider_name": "Updated Clinic"
    }

    response = client.put(
        f"/{ecosystem_id}",
        json=payload
    )

    assert response.status_code == 200

    assert response.json()["provider_name"] == "Updated Clinic"

@patch(
    "app.api.v1.endpoints.community_ecosystem.update_ecosystem_service"
)
def test_update_provider_not_found(mock_service):

    mock_service.side_effect = HTTPException(
        status_code=404,
        detail="Provider not found"
    )

    response = client.put(
        f"/{uuid4()}",
        json={"provider_name": "Updated"}
    )

    assert response.status_code == 404

@patch(
    "app.api.v1.endpoints.community_ecosystem.delete_ecosystem_service"
)
def test_delete_provider_success(mock_service):

    mock_service.return_value = {
        "message": "Provider deleted successfully"
    }

    response = client.delete(f"/{uuid4()}")

    assert response.status_code == 200

    assert response.json() == {
        "message": "Provider deleted successfully"
    }

@patch(
    "app.api.v1.endpoints.community_ecosystem.delete_ecosystem_service"
)
def test_delete_provider_not_found(mock_service):

    mock_service.side_effect = HTTPException(
        status_code=404,
        detail="Provider not found"
    )

    response = client.delete(f"/{uuid4()}")

    assert response.status_code == 404

def test_get_provider_invalid_uuid():

    response = client.get("/invalid-uuid")

    assert response.status_code == 422