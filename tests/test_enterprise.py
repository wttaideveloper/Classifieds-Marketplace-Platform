def test_create_enterprise(client):

    payload = {
        "business_short_name": "ABC",
        "business_legal_name": "ABC Pvt Ltd",
        "business_email": "abc@gmail.com"
    }

    response = client.post(
        "/api/enterprises",
        json=payload
    )

    assert response.status_code == 201

    data = response.json()

    assert data["business_short_name"] == "ABC"

def test_get_enterprises(client):

    response = client.get(
        "/api/enterprises"
    )

    assert response.status_code == 200

def test_get_enterprise_not_found(client):

    response = client.get(
        "/api/enterprises/11111111-1111-1111-1111-111111111111"
    )

    assert response.status_code == 404

