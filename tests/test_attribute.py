from uuid import uuid4
from unittest.mock import patch

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.v1.endpoints.attribute import router


app = FastAPI()
app.include_router(router, prefix="/attributes")

client = TestClient(app)


@patch(
    "app.api.v1.endpoints.attribute.create_attribute_service"
)
def test_create_attribute(
    mock_create_attribute_service
):

    attribute_id = str(uuid4())
    entity_id = str(uuid4())

    mock_create_attribute_service.return_value = {
        "id": attribute_id,
        "entity_type": "enterprise",
        "entity_id": entity_id,
        "attribute_name": "License Number",
        "attribute_value": "LIC-12345",
        "attribute_type": "text"
    }

    payload = {
        "entity_type": "enterprise",
        "entity_id": entity_id,
        "attribute_name": "License Number",
        "attribute_value": "LIC-12345",
        "attribute_type": "text"
    }

    response = client.post(
        "/attributes",
        json=payload
    )

    assert response.status_code == 201

    data = response.json()

    assert data["attribute_name"] == "License Number"
    assert data["attribute_value"] == "LIC-12345"

def test_create_attribute_validation_error():

    payload = {
        "entity_type": "enterprise"
    }

    response = client.post(
        "/attributes",
        json=payload
    )

    assert response.status_code == 422

@patch(
    "app.api.v1.endpoints.attribute.get_attributes_service"
)
def test_get_attributes(
    mock_get_attributes_service
):

    entity_id = str(uuid4())

    mock_get_attributes_service.return_value = [
        {
            "id": str(uuid4()),
            "entity_type": "enterprise",
            "entity_id": entity_id,
            "attribute_name": "License Number",
            "attribute_value": "LIC-12345",
            "attribute_type": "text"
        }
    ]

    response = client.get(
        f"/attributes?entity_type=enterprise&entity_id={entity_id}"
    )

    assert response.status_code == 200

    data = response.json()

    assert len(data) == 1
    assert data[0]["attribute_name"] == "License Number"

@patch(
    "app.api.v1.endpoints.attribute.get_attributes_service"
)
def test_get_attributes_empty(
    mock_get_attributes_service
):

    entity_id = str(uuid4())

    mock_get_attributes_service.return_value = []

    response = client.get(
        f"/attributes?entity_type=enterprise&entity_id={entity_id}"
    )

    assert response.status_code == 200
    assert response.json() == []

def test_get_attributes_missing_query_params():

    response = client.get(
        "/attributes"
    )

    assert response.status_code == 422

@patch(
    "app.api.v1.endpoints.attribute.update_attribute_service"
)
def test_update_attribute(
    mock_update_attribute_service
):

    attribute_id = str(uuid4())

    mock_update_attribute_service.return_value = {
        "id": attribute_id,
        "entity_type": "enterprise",
        "entity_id": str(uuid4()),
        "attribute_name": "License Number",
        "attribute_value": "LIC-99999",
        "attribute_type": "text"
    }

    payload = {
        "attribute_value": "LIC-99999"
    }

    response = client.put(
        f"/attributes/{attribute_id}",
        json=payload
    )

    assert response.status_code == 200

    data = response.json()

    assert data["attribute_value"] == "LIC-99999"

@patch(
    "app.api.v1.endpoints.attribute.update_attribute_service"
)
def test_update_attribute_not_found(
    mock_update_attribute_service
):

    mock_update_attribute_service.side_effect = HTTPException(
        status_code=404,
        detail="Attribute not found"
    )

    response = client.put(
        f"/attributes/{uuid4()}",
        json={
            "attribute_value": "TEST"
        }
    )

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Attribute not found"
    }

def test_update_attribute_invalid_uuid():

    response = client.put(
        "/attributes/invalid-id",
        json={
            "attribute_value": "TEST"
        }
    )

    assert response.status_code == 422

@patch(
    "app.api.v1.endpoints.attribute.delete_attribute_service"
)
def test_delete_attribute(
    mock_delete_attribute_service
):

    mock_delete_attribute_service.return_value = None

    response = client.delete(
        f"/attributes/{uuid4()}"
    )

    assert response.status_code == 200

    assert response.json() == {
        "message": "Attribute deleted successfully"
    }

@patch(
    "app.api.v1.endpoints.attribute.delete_attribute_service"
)
def test_delete_attribute_not_found(
    mock_delete_attribute_service
):

    mock_delete_attribute_service.side_effect = HTTPException(
        status_code=404,
        detail="Attribute not found"
    )

    response = client.delete(
        f"/attributes/{uuid4()}"
    )

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Attribute not found"
    }

def test_delete_attribute_invalid_uuid():

    response = client.delete(
        "/attributes/invalid-id"
    )

    assert response.status_code == 422