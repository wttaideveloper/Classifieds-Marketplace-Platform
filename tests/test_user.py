from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

# Test Register
def test_register_user():
    response = client.post("/api/v1/users/register", json={
        "username": "testuser",
        "email": "testuser@gmail.com",
        "password": "123456"
    })
    assert response.status_code == 201


# Test Login
def test_login_user():
    response = client.post("/api/v1/users/login", json={
        "email": "testuser@gmail.com",
        "password": "123456"
    })
    assert response.status_code == 200
    assert "access_token" in response.json()


# Test Protected Route (WITH token)
def test_get_profile():
    login = client.post("/api/v1/users/login", json={
        "email": "testuser@gmail.com",
        "password": "123456"
    })

    token = login.json()["access_token"]

    response = client.get(
        "/api/v1/users/profile",
        headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200


# Test Protected Route (WITHOUT token)
def test_profile_unauthorized():
    response = client.get("/api/v1/users/profile")
    assert response.status_code == 403 or response.status_code == 401

def test_register_duplicate():
    response = client.post("/api/v1/users/register", json={
        "username": "testuser",
        "email": "testuser@gmail.com",
        "password": "123456"
    })
    assert response.status_code == 400

def test_login_wrong_password():
    response = client.post("/api/v1/users/login", json={
        "email": "testuser@gmail.com",
        "password": "wrongpass"
    })
    assert response.status_code == 401