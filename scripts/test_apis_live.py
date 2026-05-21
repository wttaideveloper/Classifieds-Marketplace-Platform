"""Live API smoke test against seeded PostgreSQL data."""
import json
import uuid
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"

# Seeded IDs
BUSINESS_HEALTH = "3acd54b8-3d57-4467-bb02-dc3a5b241686"
BUSINESS_TECH = "4944b1f1-3b4e-485f-80a8-1015297e4bda"
CUSTOMER_AMIT = "c5ae06a8-ce97-4d88-b1b3-fc1e9903c757"
MERCHANT_HEALTH = "88e4f5f8-6a8f-456b-8d6e-4c25d4c92644"
LISTING_CHECKUP = "c099161a-051a-4762-b608-b177aa61caad"
LISTING_PYTHON = "6c060d72-3b05-4911-89f8-14a7d407ebd7"
ORDER_PENDING = "31f7cdb0-7d26-46da-bfe8-c8caaf1ad137"
ORDER_CONFIRMED = "9931353a-9f60-40c9-b61a-16faf58fbbfb"
REVIEW_PENDING = "44578d32-411c-4168-acca-fdb1b6b2603e"


def request(method, path, data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = raw
        return e.code, detail


def login_customer():
    status, body = request(
        "POST",
        "/auth/customer/login",
        {"email": "amit@example.com", "password": "Password@123"},
    )
    assert status == 200, body
    return body["access_token"]


def login_admin():
    status, body = request(
        "POST",
        "/auth/admin/login",
        {"email": "admin@example.com", "password": "admin123"},
    )
    assert status == 200, body
    return body["accessToken"]


def main():
    print("=== Health ===")
    req = urllib.request.Request("http://127.0.0.1:8000/health")
    with urllib.request.urlopen(req, timeout=10) as resp:
        print(resp.status, json.loads(resp.read().decode("utf-8")))

    customer_token = login_customer()
    admin_token = login_admin()
    print("Customer + admin login OK")

    print("\n=== GET reviews (Priya Health Clinic) ===")
    s, b = request("GET", f"/reviews/{BUSINESS_HEALTH}")
    print(s, json.dumps(b, indent=2, default=str))
    assert s == 200 and b["total_elements"] == 2 and b["average_rating"] == 4.5

    print("\n=== GET reviews filtered rating=5 ===")
    s, b = request("GET", f"/reviews/{BUSINESS_HEALTH}?rating=5")
    print(s, json.dumps(b, indent=2, default=str))
    assert s == 200 and b["total_elements"] == 1

    print("\n=== GET reviews (Rahul Tech - pending only, expect 0 approved) ===")
    s, b = request("GET", f"/reviews/{BUSINESS_TECH}")
    print(s, json.dumps(b, indent=2, default=str))
    assert s == 200 and b["total_elements"] == 0

    print("\n=== GET order by id ===")
    s, b = request("GET", f"/orders/{ORDER_PENDING}")
    print(s, json.dumps(b, indent=2, default=str))
    assert s == 200 and b["data"]["order_status"] == "Pending"

    print("\n=== GET customer orders ===")
    s, b = request("GET", f"/orders?customer_id={CUSTOMER_AMIT}&page=1&size=10")
    print(s, json.dumps(b, indent=2, default=str))
    assert s == 200 and b["total_elements"] >= 1

    print("\n=== GET merchant orders ===")
    s, b = request("GET", f"/orders?merchant_id={MERCHANT_HEALTH}&page=1&size=10")
    print(s, json.dumps(b, indent=2, default=str))
    assert s == 200 and b["total_elements"] >= 1

    print("\n=== POST new review (customer auth) ===")
    s, b = request(
        "POST",
        "/reviews",
        {
            "business_id": BUSINESS_TECH,
            "booking_id": str(uuid.uuid4()),
            "rating": 5,
            "review_comment": "Live API test review",
            "listing_id": LISTING_PYTHON,
        },
        token=customer_token,
    )
    print(s, json.dumps(b, indent=2, default=str))
    assert s == 201
    new_review_id = b["review_id"]

    print("\n=== PUT approve pending review (admin auth) ===")
    s, b = request(
        "PUT",
        f"/admin/reviews/{REVIEW_PENDING}",
        {"moderation_status": "Approved", "remarks": "Live test approval"},
        token=admin_token,
    )
    print(s, json.dumps(b, indent=2, default=str))
    assert s == 200 and b["moderation_status"] == "Approved"

    print("\n=== GET tech reviews after approval (expect 1+) ===")
    s, b = request("GET", f"/reviews/{BUSINESS_TECH}")
    print(s, json.dumps(b, indent=2, default=str))
    assert s == 200 and b["total_elements"] >= 1

    print("\n=== POST new order ===")
    s, b = request(
        "POST",
        f"/orders?customer_id={CUSTOMER_AMIT}",
        {
            "business_id": BUSINESS_TECH,
            "items": [{"listing_id": LISTING_PYTHON, "quantity": 1}],
            "payment_method": "UPI",
            "notes": "Live API order test",
        },
    )
    print(s, json.dumps(b, indent=2, default=str))
    assert s == 200 and b["success"] is True
    new_order_id = b["data"]["order_id"]

    print("\n=== PUT order status Confirmed ===")
    s, b = request(
        "PUT",
        f"/orders/{new_order_id}/status?merchant_id={MERCHANT_HEALTH}",
        {"order_status": "Confirmed", "remarks": "Wrong merchant - expect fail"},
    )
    print(s, json.dumps(b, indent=2, default=str))
    assert s != 200  # merchant mismatch

    s, b = request(
        "PUT",
        f"/orders/{ORDER_PENDING}/status?merchant_id={MERCHANT_HEALTH}",
        {"order_status": "Confirmed", "remarks": "Confirmed via API test"},
    )
    print(s, json.dumps(b, indent=2, default=str))
    assert s == 200

    print("\n=== All live API checks passed ===")
    print(f"Created review: {new_review_id}")
    print(f"Created order: {new_order_id}")


if __name__ == "__main__":
    main()
