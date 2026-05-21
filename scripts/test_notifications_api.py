"""Smoke test notification APIs on dev_fazil."""
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"


def req(method, path, data=None, token=None):
    headers = {"Content-Type": "application/json", "accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode("utf-8") if data is not None else None
    request = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"detail": raw}


def main():
    s, b = req("POST", "/customer/login", {"email": "amit@example.com", "password": "Password@123"})
    assert s == 200, b
    customer_token = b.get("accessToken") or b.get("access_token")

    s, b = req("POST", "/admin/login", {"email": "admin@example.com", "password": "admin123"})
    assert s == 200, b
    admin_token = b["accessToken"]

    customer_id = "c5ae06a8-ce97-4d88-b1b3-fc1e9903c757"

    s, b = req("POST", "/notifications", {
        "user_id": customer_id,
        "user_role": "customer",
        "title": "Test",
        "message": "Hello",
        "notification_type": "System",
    })
    assert s == 401, b

    s, b = req("POST", "/notifications", {
        "user_id": customer_id,
        "user_role": "customer",
        "title": "System alert",
        "message": "Your order was updated.",
        "notification_type": "System",
    }, token=admin_token)
    assert s == 201, b
    nid = b["data"]["notification_id"]

    s, b = req("GET", "/notifications?page=1&size=10", token=customer_token)
    assert s == 200 and b["total_elements"] >= 1, b

    s, b = req("GET", f"/notifications/{nid}", token=customer_token)
    assert s == 200, b

    s, b = req("PUT", f"/notifications/{nid}/read", token=customer_token)
    assert s == 200 and b["data"]["is_read"] is True, b

    s, b = req("DELETE", f"/notifications/{nid}", token=customer_token)
    assert s == 200, b

    s, b = req("GET", f"/notifications/{nid}", token=customer_token)
    assert s == 404, b

    print("All notification API checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
