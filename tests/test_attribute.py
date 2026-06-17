def test_create_attribute(
    client,
    enterprise_id
):

    payload = {
        "entity_type": "enterprise",
        "entity_id": enterprise_id,
        "attribute_name": "License",
        "attribute_value": "LIC123",
        "attribute_type": "text"
    }

    response = client.post(
        "/api/attributes",
        json=payload
    )

    assert response.status_code == 201

def test_get_attributes(
    client,
    enterprise_id
):

    response = client.get(
        f"/api/attributes?entity_type=enterprise&entity_id={enterprise_id}"
    )

    assert response.status_code == 200