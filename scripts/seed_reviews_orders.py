"""Seed sample reviews and orders for API testing."""
import uuid
import psycopg2
from datetime import datetime

conn = psycopg2.connect(
    dbname="wisdomdatabase",
    user="postgres",
    password="fazil2004",
    host="localhost",
    port="5432",
)
cur = conn.cursor()

# Resolve seeded IDs
cur.execute('SELECT id FROM customers WHERE email = %s', ('amit@example.com',))
customer_id = cur.fetchone()[0]
cur.execute('SELECT id FROM customers WHERE email = %s', ('sneha@example.com',))
customer2_id = cur.fetchone()[0]
cur.execute('SELECT id FROM businesses WHERE name = %s', ('Priya Health Clinic',))
business_health = cur.fetchone()[0]
cur.execute('SELECT id FROM businesses WHERE name = %s', ('Rahul Tech Academy',))
business_tech = cur.fetchone()[0]
cur.execute('SELECT id FROM merchant_listings WHERE title = %s', ('General Health Checkup',))
listing_checkup = cur.fetchone()[0]
cur.execute('SELECT id FROM merchant_listings WHERE title = %s', ('Python Programming Course',))
listing_python = cur.fetchone()[0]
cur.execute('SELECT id FROM merchants WHERE "businessEmail" = %s', ('priya@healthclinic.com',))
merchant_health = cur.fetchone()[0]
cur.execute('SELECT id FROM merchants WHERE "businessEmail" = %s', ('rahul@techacademy.com',))
merchant_tech = cur.fetchone()[0]
cur.execute('SELECT id FROM admins WHERE email = %s', ('admin@example.com',))
admin_id = cur.fetchone()[0]

now = datetime.utcnow()

reviews = [
    (
        str(uuid.uuid4()),
        business_health,
        customer_id,
        str(uuid.uuid4()),
        listing_checkup,
        5,
        "Excellent checkup, very professional staff.",
        "Approved",
        True,
    ),
    (
        str(uuid.uuid4()),
        business_health,
        customer2_id,
        str(uuid.uuid4()),
        listing_checkup,
        4,
        "Good service, slight wait time.",
        "Approved",
        False,
    ),
    (
        str(uuid.uuid4()),
        business_tech,
        customer_id,
        str(uuid.uuid4()),
        listing_python,
        3,
        "Course content is okay but needs more projects.",
        "Pending",
        False,
    ),
]

for rid, bid, cid, booking_id, lid, rating, comment, status, verified in reviews:
    cur.execute(
        "SELECT id FROM reviews WHERE booking_id = %s",
        (booking_id,),
    )
    if cur.fetchone():
        continue
    cur.execute(
        """
        INSERT INTO reviews (
            id, business_id, customer_id, booking_id, listing_id,
            rating, review_comment, moderation_status, is_verified, created_at, updated_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (rid, bid, cid, booking_id, lid, rating, comment, status, verified, now, now),
    )
    if status != "Pending":
        cur.execute(
            """
            INSERT INTO review_moderation_history (
                id, review_id, old_status, new_status, moderated_by, remarks, created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                str(uuid.uuid4()),
                rid,
                "Pending",
                status,
                admin_id,
                "Seeded for API testing",
                now,
            ),
        )
    print(f"Review {rating}* -> {status}")

order1_id = str(uuid.uuid4())
order2_id = str(uuid.uuid4())
orders = [
    (
        order1_id,
        f"ORD-SEED-001-{order1_id[:8].upper()}",
        customer_id,
        merchant_health,
        business_health,
        999.0,
        0.0,
        0.0,
        0.0,
        999.0,
        "Pending",
        "Pending",
        "UPI",
        "First test order",
    ),
    (
        order2_id,
        f"ORD-SEED-002-{order2_id[:8].upper()}",
        customer2_id,
        merchant_tech,
        business_tech,
        4999.0,
        0.0,
        0.0,
        0.0,
        4999.0,
        "Confirmed",
        "Paid",
        "Card",
        "Python course enrollment",
    ),
]

for row in orders:
    cur.execute("SELECT id FROM orders WHERE order_number = %s", (row[1],))
    if cur.fetchone():
        print(f"Order exists: {row[1]}")
        continue
    cur.execute(
        """
        INSERT INTO orders (
            id, order_number, customer_id, merchant_id, business_id,
            total_amount, tax_amount, shipping_amount, discount_amount, final_amount,
            order_status, payment_status, payment_method, notes, created_at, updated_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (*row, now, now),
    )
    print(f"Order {row[1]} -> {row[10]}")

items = [
    (str(uuid.uuid4()), order1_id, listing_checkup, "General Health Checkup", "service", 1, 999.0, 999.0),
    (str(uuid.uuid4()), order2_id, listing_python, "Python Programming Course", "training", 1, 4999.0, 4999.0),
]
for item in items:
    cur.execute("SELECT id FROM order_items WHERE order_id = %s AND listing_id = %s", (item[1], item[2]))
    if cur.fetchone():
        continue
    cur.execute(
        """
        INSERT INTO order_items (
            id, order_id, listing_id, listing_name, listing_type,
            quantity, unit_price, total_price, created_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (*item, now),
    )

history = [
    (str(uuid.uuid4()), order1_id, "Pending", "Pending", None, "Order created"),
    (str(uuid.uuid4()), order2_id, "Pending", "Pending", None, "Order created"),
    (str(uuid.uuid4()), order2_id, "Pending", "Confirmed", merchant_tech, "Payment received"),
]
for h in history:
    cur.execute(
        """
        INSERT INTO order_status_history (
            id, order_id, old_status, new_status, updated_by, remarks, created_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s)
        """,
        (*h, now),
    )

conn.commit()
cur.close()
conn.close()
print("Reviews and orders seed complete.")
