def test_create_service(
    client,
    enterprise_id
):

    payload = {
        "enterprise_id": enterprise_id,
        "service_name": "Consulting",
        "service_category": "Business",
        "service_price": 1000,
        "duration": 60
    }

    response = client.post(
        "/api/services",
        json=payload
    )

    assert response.status_code == 201

def test_service_not_found(client):

    response = client.get(
        "/api/services/11111111-1111-1111-1111-111111111111"
    )

    assert response.status_code == 404

