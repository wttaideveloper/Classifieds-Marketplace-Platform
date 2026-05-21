"""Dump reviews, orders, and related IDs for API testing."""
import json
import psycopg2
from decimal import Decimal
from datetime import datetime, date


def default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    return str(o)


conn = psycopg2.connect(
    dbname="wisdomdatabase",
    user="postgres",
    password="fazil2004",
    host="localhost",
    port="5432",
)
cur = conn.cursor()

for t in [
    "reviews",
    "review_moderation_history",
    "orders",
    "order_items",
    "order_status_history",
]:
    cur.execute(f"SELECT COUNT(*) FROM {t}")
    print(f"{t}: {cur.fetchone()[0]} rows")

queries = {
    "reviews": """
        SELECT r.id, r.business_id, r.customer_id, r.booking_id, r.listing_id,
               r.rating, r.review_comment, r.moderation_status, r.is_verified, r.created_at,
               c."firstName" || ' ' || c."lastName" AS customer_name
        FROM reviews r
        LEFT JOIN customers c ON c.id = r.customer_id
        ORDER BY r.created_at DESC
    """,
    "review_moderation_history": """
        SELECT * FROM review_moderation_history ORDER BY created_at DESC
    """,
    "orders": """
        SELECT id, order_number, customer_id, merchant_id, business_id,
               total_amount, tax_amount, shipping_amount, discount_amount, final_amount,
               order_status, payment_status, payment_method, notes, created_at
        FROM orders ORDER BY created_at DESC
    """,
    "order_items": """
        SELECT id, order_id, listing_id, listing_name, listing_type,
               quantity, unit_price, total_price, created_at
        FROM order_items ORDER BY created_at DESC
    """,
    "order_status_history": """
        SELECT * FROM order_status_history ORDER BY created_at DESC
    """,
    "customers": """
        SELECT id, "firstName", "lastName", email, "mobileNumber", status
        FROM customers ORDER BY email
    """,
    "merchants": """
        SELECT id, "fullName", "businessEmail", "businessName", status
        FROM merchants ORDER BY "businessEmail"
    """,
    "businesses": """
        SELECT b.id, b.name, b.category, b.status, b.merchant_id, m."businessEmail"
        FROM businesses b
        JOIN merchants m ON m.id = b.merchant_id
        ORDER BY b.name
    """,
    "merchant_listings": """
        SELECT id, "businessId", "listingType", title, price, currency, status
        FROM merchant_listings
        ORDER BY title
    """,
}

out = {}
for key, sql in queries.items():
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    out[key] = [dict(zip(cols, row)) for row in cur.fetchall()]

print(json.dumps(out, indent=2, default=default))
cur.close()
conn.close()
