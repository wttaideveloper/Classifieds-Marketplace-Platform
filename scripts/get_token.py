from fastapi.testclient import TestClient
from app.main import app
import json

client = TestClient(app)

# Try register (ignore response)
client.post("/api/v1/users/register", json={
    "username": "testuser",
    "email": "testuser@gmail.com",
    "password": "123456"
})

res = client.post("/api/v1/users/login", json={
    "email": "testuser@gmail.com",
    "password": "123456"
})

print(json.dumps({"status": res.status_code, "body": res.json()}))
