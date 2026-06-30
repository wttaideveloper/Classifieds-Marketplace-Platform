from unittest.mock import patch


@patch("app.api.v1.endpoints.enterprise.create_enterprise_service")
def test_create_enterprise(mock_create_enterprise_service, client):
    mock_create_enterprise_service.return_value = {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "business_short_name": "ABC",
        "business_legal_name": "ABC Pvt Ltd",
        "business_email": "abc@gmail.com",
        "status": "draft",
    }

    payload = {
        "business_short_name": "ABC",
        "business_legal_name": "ABC Pvt Ltd",
        "business_email": "abc@gmail.com",
    }

    response = client.post(
        "/api/v1/enterprises/",
        json=payload,
    )

    assert response.status_code == 201
    assert response.json()["business_short_name"] == "ABC"


@patch("app.api.v1.endpoints.enterprise.get_all_enterprises_service")
def test_get_enterprises(mock_get_all_enterprises_service, client):
    mock_get_all_enterprises_service.return_value = {
        "items": [
            {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "business_short_name": "ABC",
                "business_legal_name": "ABC Pvt Ltd",
                "business_email": "abc@gmail.com",
                "status": "active",
                "status_label": "active",
            }
        ],
        "pagination": {
            "total": 1,
            "page": 1,
            "page_size": 20,
            "total_pages": 1,
        },
    }

    response = client.get("/api/v1/enterprises/")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1


@patch("app.api.v1.endpoints.enterprise.get_enterprise_service")
def test_get_enterprise_not_found(mock_get_enterprise_service, client):
    from fastapi import HTTPException

    mock_get_enterprise_service.side_effect = HTTPException(
        status_code=404,
        detail="Enterprise not found",
    )

    response = client.get(
        "/api/v1/enterprises/11111111-1111-1111-1111-111111111111",
    )

    assert response.status_code == 404
