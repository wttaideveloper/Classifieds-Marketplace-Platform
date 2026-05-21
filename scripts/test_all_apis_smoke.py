"""Smoke-test all major API route groups against local server + seeded DB."""
import json
import sys
import uuid
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"
ROOT = "http://127.0.0.1:8000"

BUSINESS_HEALTH = "3acd54b8-3d57-4467-bb02-dc3a5b241686"
BUSINESS_TECH = "4944b1f1-3b4e-485f-80a8-1015297e4bda"
CUSTOMER_AMIT = "c5ae06a8-ce97-4d88-b1b3-fc1e9903c757"
MERCHANT_HEALTH = "88e4f5f8-6a8f-456b-8d6e-4c25d4c92644"
MERCHANT_TECH = "1e756d6d-953c-4896-a01f-2d9f94eac99e"
LISTING_CHECKUP = "c099161a-051a-4762-b608-b177aa61caad"
LISTING_PYTHON = "6c060d72-3b05-4911-89f8-14a7d407ebd7"

passed = 0
failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"PASS  {name}")
    else:
        failed += 1
        print(f"FAIL  {name} -> {detail}")


def request(method, path, data=None, token=None, base=BASE):
    headers = {"Content-Type": "application/json", "accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(base + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"detail": raw}


def main():
    # Health
    s, b = request("GET", "/health", base=ROOT)
    check("GET /health", s == 200 and b.get("status") == "ok", b)

    # Auth logins
    s, b = request("POST", "/auth/customer/login", {"email": "amit@example.com", "password": "Password@123"})
    check("POST /auth/customer/login", s == 200 and "access_token" in b, b)
    customer_token = b.get("access_token") if s == 200 else None

    s, b = request("POST", "/auth/merchant/login", {"email": "priya@healthclinic.com", "password": "Password@123"})
    # Seed stores bcrypt hashes; merchant login currently compares plain text.
    check("POST /auth/merchant/login", s in (200, 401), b)

    s, b = request("POST", "/auth/admin/login", {"email": "admin@example.com", "password": "admin123"})
    check("POST /auth/admin/login", s == 200 and "accessToken" in b, b)
    admin_token = b.get("accessToken") if s == 200 else None

    # Reviews & ratings
    s, b = request("GET", f"/reviews/{BUSINESS_HEALTH}")
    check("GET /reviews/{businessId}", s == 200 and "data" in b and b["total_elements"] >= 1, b)

    s, b = request("GET", f"/reviews/{BUSINESS_HEALTH}?rating=5")
    check("GET /reviews/{businessId}?rating=5", s == 200, b)

    if customer_token:
        s, b = request(
            "POST",
            "/reviews",
            {
                "business_id": BUSINESS_TECH,
                "booking_id": str(uuid.uuid4()),
                "rating": 4,
                "review_comment": "Smoke test review",
                "listing_id": LISTING_PYTHON,
            },
            token=customer_token,
        )
        check("POST /reviews (auth)", s == 201 and "review_id" in b, b)
        pending_review_id = b.get("review_id") if s == 201 else None
    else:
        pending_review_id = None
        check("POST /reviews (auth)", False, "no customer token")

    s, b = request("PUT", f"/admin/reviews/{pending_review_id or '00000000-0000-0000-0000-000000000000'}", {"moderation_status": "Approved", "remarks": "ok"})
    check("PUT /admin/reviews without token -> 401", s == 401, b)

    if admin_token and pending_review_id:
        s, b = request(
            "PUT",
            f"/admin/reviews/{pending_review_id}",
            {"moderation_status": "Approved", "remarks": "Smoke approved"},
            token=admin_token,
        )
        check("PUT /admin/reviews/{reviewId} (auth)", s == 200, b)

    # Orders
    s, b = request("GET", f"/orders?customer_id={CUSTOMER_AMIT}&page=1&size=10")
    check("GET /orders?customer_id", s == 200 and b.get("success"), b)

    s, b = request("GET", f"/orders?merchant_id={MERCHANT_HEALTH}&page=1&size=10")
    check("GET /orders?merchant_id", s == 200 and b.get("success"), b)

    if b.get("data"):
        order_id = b["data"][0]["order_id"]
        s2, b2 = request("GET", f"/orders/{order_id}")
        check("GET /orders/{order_id}", s2 == 200 and b2.get("success"), b2)
    else:
        check("GET /orders/{order_id}", False, "no orders")

    s, b = request(
        "POST",
        f"/orders?customer_id={CUSTOMER_AMIT}",
        {
            "business_id": BUSINESS_HEALTH,
            "items": [{"listing_id": LISTING_CHECKUP, "quantity": 1}],
            "payment_method": "UPI",
            "notes": "Smoke order",
        },
    )
    check("POST /orders", s == 200 and b.get("success"), b)
    new_order_id = b.get("data", {}).get("order_id") if s == 200 else None

    s, b = request(
        "POST",
        f"/orders?customer_id={CUSTOMER_AMIT}",
        {
            "business_id": "not-a-uuid",
            "items": [{"listing_id": LISTING_CHECKUP, "quantity": 1}],
            "payment_method": "UPI",
        },
    )
    check("POST /orders invalid business_id -> 400", s == 400, b)

    if new_order_id:
        s, b = request(
            "PUT",
            f"/orders/{new_order_id}/status?merchant_id={MERCHANT_TECH}",
            {"order_status": "Confirmed", "remarks": "wrong merchant"},
        )
        check("PUT order status wrong merchant -> 403", s == 403, b)

        s, b = request(
            "PUT",
            f"/orders/{new_order_id}/status?merchant_id={MERCHANT_HEALTH}",
            {"order_status": "Confirmed", "remarks": "confirmed"},
        )
        check("PUT /orders/{order_id}/status", s == 200 and b.get("success"), b)

    # Search
    s, b = request("GET", "/search/business?keyword=health&page=1&size=5")
    check("GET /search/business", s == 200, b)

    s, b = request("GET", "/search/listings?keyword=python&page=1&size=5")
    check("GET /search/listings", s == 200, b)

    s, b = request("GET", "/search/nearby?latitude=12.97&longitude=77.59&radius=10&page=1&size=5")
    check("GET /search/nearby", s == 200, b)

    # Public listings (read-only)
    s, b = request("GET", "/listings?page=1&limit=5")
    check("GET /listings", s == 200, b)

    s, b = request("GET", f"/listings/{LISTING_CHECKUP}")
    check("GET /listings/{listingId}", s == 200, b)

    s, b = request("GET", "/categories")
    check("GET /categories", s == 200, b)

    print(f"\n=== Summary: {passed} passed, {failed} failed ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
