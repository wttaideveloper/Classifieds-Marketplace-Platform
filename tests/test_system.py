def test_health(client):

    response = client.get(
        "/api/health"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "healthy"

def test_inventory(client):

    response = client.get(
        "/api/inventory"
    )

    assert response.status_code == 200

    data = response.json()

    assert "apis" in data