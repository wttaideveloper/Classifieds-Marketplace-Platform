from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_create_communication():

    payload = {
        "customer_name": "Riddhi",
        "customer_email": "riddhi@test.com",
        "subject": "Issue with payment",
        "message": "Unable to complete payment"
    }

    response = client.post(
        "/communications",
        json=payload
    )

    assert response.status_code == 201

    data = response.json()

    assert data["customer_name"] == "Riddhi"
    assert data["status"] == "OPEN"


def test_get_all_communications():

    response = client.get(
        "/communications"
    )

    assert response.status_code == 200


def test_get_communication_not_found():

    response = client.get(
        "/communications/11111111-1111-1111-1111-111111111111"
    )

    assert response.status_code == 404

    assert (
        response.json()["detail"]
        == "Communication not found"
    )


def test_update_communication():

    payload = {
        "customer_name": "Test",
        "customer_email": "test@test.com",
        "subject": "Test Subject",
        "message": "Test Message"
    }

    create_response = client.post(
        "/communications",
        json=payload
    )

    communication_id = create_response.json()["id"]

    update_payload = {
        "status": "CLOSED"
    }

    response = client.put(
        f"/communications/{communication_id}",
        json=update_payload
    )

    assert response.status_code == 200

    assert (
        response.json()["status"]
        == "CLOSED"
    )


def test_delete_communication():

    payload = {
        "customer_name": "Delete User",
        "customer_email": "delete@test.com",
        "subject": "Delete Test",
        "message": "Delete Message"
    }

    create_response = client.post(
        "/communications",
        json=payload
    )

    communication_id = create_response.json()["id"]

    response = client.delete(
        f"/communications/{communication_id}"
    )

    assert response.status_code == 200

    assert (
        response.json()["message"]
        == "Communication deleted successfully"
    )