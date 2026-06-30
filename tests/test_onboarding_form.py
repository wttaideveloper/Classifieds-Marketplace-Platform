from datetime import datetime
from uuid import uuid4
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.v1.endpoints.onboarding_form import router


app = FastAPI()
app.include_router(router, prefix="/onboarding-forms")
client = TestClient(app)

_FORM_ID = "550e8400-e29b-41d4-a716-446655440000"
_NOW = datetime.utcnow().isoformat() + "Z"

_SAMPLE_SECTION = {
    "id": "550e8400-e29b-41d4-a716-446655440001",
    "title": "Business Info",
    "order": 1,
    "fields": [
        {
            "id": "550e8400-e29b-41d4-a716-446655440002",
            "label": "Enterprise Name",
            "field_key": "business_legal_name",
            "field_type": "text",
            "placeholder": "Pinnacle Wellness Co.",
            "help_text": "Legal enterprise name",
            "required": True,
            "locked": True,
            "visible": True,
            "order": 1,
            "options": [],
        },
        {
            "id": "550e8400-e29b-41d4-a716-446655440003",
            "label": "Trading / DBA Name",
            "field_key": "business_short_name",
            "field_type": "text",
            "placeholder": "Pinnacle Wellness",
            "help_text": "Short business name",
            "required": False,
            "locked": True,
            "visible": True,
            "order": 2,
            "options": [],
        },
    ],
}

_FORM_DETAIL = {
    "id": _FORM_ID,
    "name": "Standard Enterprise Onboarding Form",
    "description": "Default onboarding form",
    "entity_type": "enterprise",
    "status": "draft",
    "sections": [_SAMPLE_SECTION],
    "created_at": _NOW,
    "updated_at": _NOW,
    "published_at": None,
}

_CREATE_PAYLOAD = {
    "name": "Standard Enterprise Onboarding Form",
    "description": "Default onboarding form",
    "entity_type": "enterprise",
    "status": "draft",
    "sections": [
        {
            "title": "Business Info",
            "order": 1,
            "fields": [
                {
                    "label": "Enterprise Name",
                    "field_key": "business_legal_name",
                    "field_type": "text",
                    "required": True,
                    "locked": True,
                    "visible": True,
                    "order": 1,
                    "options": [],
                },
                {
                    "label": "Trading / DBA Name",
                    "field_key": "business_short_name",
                    "field_type": "text",
                    "required": False,
                    "locked": True,
                    "visible": True,
                    "order": 2,
                    "options": [],
                },
            ],
        }
    ],
}


@patch("app.api.v1.endpoints.onboarding_form.create_onboarding_form_service")
def test_create_onboarding_form(mock_create_service):
    mock_create_service.return_value = _FORM_DETAIL

    response = client.post("/onboarding-forms/", json=_CREATE_PAYLOAD)

    assert response.status_code == 201
    assert response.json()["name"] == "Standard Enterprise Onboarding Form"


@patch("app.api.v1.endpoints.onboarding_form.list_onboarding_forms_service")
def test_list_onboarding_forms(mock_list_service):
    mock_list_service.return_value = {
        "items": [
            {
                "id": _FORM_ID,
                "name": "Standard Enterprise Onboarding Form",
                "description": "Default onboarding form",
                "entity_type": "enterprise",
                "status": "draft",
                "sections_count": 1,
                "fields_count": 2,
                "assigned_count": 0,
                "created_at": _NOW,
                "updated_at": _NOW,
            }
        ],
        "pagination": {
            "total": 1,
            "page": 1,
            "page_size": 20,
            "total_pages": 1,
        },
    }

    response = client.get("/onboarding-forms/?entity_type=enterprise")

    assert response.status_code == 200
    assert len(response.json()["items"]) == 1


@patch("app.api.v1.endpoints.onboarding_form.get_onboarding_form_service")
def test_get_onboarding_form(mock_get_service):
    mock_get_service.return_value = _FORM_DETAIL

    response = client.get(f"/onboarding-forms/{_FORM_ID}")

    assert response.status_code == 200
    assert response.json()["id"] == _FORM_ID


@patch("app.api.v1.endpoints.onboarding_form.update_onboarding_form_service")
def test_update_onboarding_form(mock_update_service):
    mock_update_service.return_value = {
        "id": _FORM_ID,
        "name": "Fitness Enterprise Onboarding Form",
        "status": "draft",
        "sections_count": 1,
        "fields_count": 2,
        "updated_at": _NOW,
    }

    response = client.put(
        f"/onboarding-forms/{_FORM_ID}",
        json={
            **_CREATE_PAYLOAD,
            "name": "Fitness Enterprise Onboarding Form",
        },
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Fitness Enterprise Onboarding Form"


@patch("app.api.v1.endpoints.onboarding_form.publish_onboarding_form_service")
def test_publish_onboarding_form(mock_publish_service):
    mock_publish_service.return_value = {
        "id": _FORM_ID,
        "status": "published",
        "published_at": _NOW,
    }

    response = client.put(
        f"/onboarding-forms/{_FORM_ID}/publish",
        json={"status": "published"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "published"


@patch("app.api.v1.endpoints.onboarding_form.delete_onboarding_form_service")
def test_delete_onboarding_form(mock_delete_service):
    mock_delete_service.return_value = {
        "id": _FORM_ID,
        "status": "inactive",
        "message": "Onboarding form deactivated successfully.",
    }

    response = client.delete(f"/onboarding-forms/{_FORM_ID}")

    assert response.status_code == 200
    assert response.json()["status"] == "inactive"


@patch("app.api.v1.endpoints.onboarding_form.get_onboarding_form_service")
def test_get_onboarding_form_not_found(mock_get_service):
    mock_get_service.side_effect = HTTPException(
        status_code=404,
        detail="Onboarding form not found",
    )

    response = client.get(f"/onboarding-forms/{uuid4()}")

    assert response.status_code == 404
