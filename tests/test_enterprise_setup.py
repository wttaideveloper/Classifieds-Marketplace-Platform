from uuid import uuid4
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_create_enterprise():
    payload = {
        "organization_name": "ABC Technologies",
        "organization_code": "ABC001",
        "industry": "IT",
        "website": "https://abc.com",
        "email": "admin@abc.com",
        "phone": "9876543210",
        "address": "Ahmedabad",
        "company_size": "500"
    }

    response = client.post(
        "/enterprise/",
        json=payload
    )

    assert response.status_code == 201

    data = response.json()

    assert data["organization_name"] == "ABC Technologies"
    assert "enterprise_id" in data


def test_get_all_enterprises():
    response = client.get("/enterprise/")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_get_enterprise_by_id():
    create_payload = {
        "organization_name": "Test Company",
        "organization_code": "TEST001"
    }

    create_response = client.post(
        "/enterprise/",
        json=create_payload
    )

    enterprise_id = create_response.json()["enterprise_id"]

    response = client.get(
        f"/enterprise/{enterprise_id}"
    )

    assert response.status_code == 200
    assert response.json()["enterprise_id"] == enterprise_id


def test_get_enterprise_not_found():
    response = client.get(
        f"/enterprise/{uuid4()}"
    )

    assert response.status_code == 404


def test_update_enterprise():
    create_payload = {
        "organization_name": "Old Company",
        "organization_code": "OLD001"
    }

    create_response = client.post(
        "/enterprise/",
        json=create_payload
    )

    enterprise_id = create_response.json()["enterprise_id"]

    update_payload = {
        "organization_name": "Updated Company"
    }

    response = client.put(
        f"/enterprise/{enterprise_id}",
        json=update_payload
    )

    assert response.status_code == 200
    assert (
        response.json()["organization_name"]
        == "Updated Company"
    )


def test_update_enterprise_not_found():
    response = client.put(
        f"/enterprise/{uuid4()}",
        json={
            "organization_name": "Test"
        }
    )

    assert response.status_code == 404


def test_delete_enterprise():
    create_payload = {
        "organization_name": "Delete Company",
        "organization_code": "DEL001"
    }

    create_response = client.post(
        "/enterprise/",
        json=create_payload
    )

    enterprise_id = create_response.json()["enterprise_id"]

    response = client.delete(
        f"/enterprise/{enterprise_id}"
    )

    assert response.status_code == 200
    assert (
        response.json()["message"]
        == "Enterprise deleted successfully"
    )


def test_delete_enterprise_not_found():
    response = client.delete(
        f"/enterprise/{uuid4()}"
    )

    assert response.status_code == 404