import psycopg2
import uuid
import bcrypt
from datetime import datetime

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

conn = psycopg2.connect(
    dbname="wisdomdatabase",
    user="postgres",
    password="fazil2004",
    host="localhost",
    port="5432"
)
cur = conn.cursor()

print("Starting seed...")

# ─── ADMIN ───────────────────────────────────────────────
cur.execute("SELECT id FROM admins WHERE email = 'admin@example.com'")
if not cur.fetchone():
    cur.execute(
        "INSERT INTO admins (name, email, password, status) VALUES (%s, %s, %s, %s)",
        ("Super Admin", "admin@example.com", hash_password("admin123"), "active")
    )
    print("✓ Admin created  →  admin@example.com / admin123")
else:
    print("- Admin already exists")

# ─── CATEGORIES ──────────────────────────────────────────
categories = [
    (str(uuid.uuid4()), "Healthcare",   "Health and medical services"),
    (str(uuid.uuid4()), "Education",    "Educational services and courses"),
    (str(uuid.uuid4()), "Food & Dining","Restaurants and food services"),
    (str(uuid.uuid4()), "Fitness",      "Gym and fitness services"),
    (str(uuid.uuid4()), "Technology",   "Tech products and services"),
]
cat_ids = {}
for cid, name, desc in categories:
    cur.execute("SELECT id FROM categories WHERE name = %s", (name,))
    row = cur.fetchone()
    if not row:
        cur.execute(
            "INSERT INTO categories (id, name, description, \"isActive\", \"isDeleted\") VALUES (%s, %s, %s, %s, %s)",
            (cid, name, desc, True, False)
        )
        cat_ids[name] = cid
        print(f"✓ Category: {name}")
    else:
        cat_ids[name] = str(row[0])
        print(f"- Category exists: {name}")

# ─── MERCHANTS ───────────────────────────────────────────
merchants = [
    (str(uuid.uuid4()), "Dr. Priya Sharma",  "priya@healthclinic.com",  "+911234567890", "Priya Health Clinic"),
    (str(uuid.uuid4()), "Rahul Verma",       "rahul@techacademy.com",   "+919876543210", "Rahul Tech Academy"),
    (str(uuid.uuid4()), "Anita Patel",       "anita@fitzone.com",       "+918765432109", "FitZone Gym"),
]
merchant_ids = {}
for mid, name, email, mobile, bname in merchants:
    cur.execute("SELECT id FROM merchants WHERE \"businessEmail\" = %s", (email,))
    row = cur.fetchone()
    if not row:
        cur.execute("""
            INSERT INTO merchants (id, \"fullName\", \"businessEmail\", \"mobileNumber\", \"businessName\",
                password, \"acceptTerms\", \"acceptPrivacyPolicy\", \"isEmailVerified\", status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (mid, name, email, mobile, bname, hash_password("Password@123"), True, True, True, "active"))
        merchant_ids[email] = mid
        print(f"✓ Merchant: {bname}  →  {email} / Password@123")
    else:
        merchant_ids[email] = str(row[0])
        print(f"- Merchant exists: {bname}")

# ─── MERCHANT PROFILES ───────────────────────────────────
profiles = [
    {
        "merchant_email": "priya@healthclinic.com",
        "businessName": "Priya Health Clinic",
        "businessDescription": "Expert healthcare services with experienced doctors",
        "primaryCategory": "Healthcare",
        "businessEmail": "priya@healthclinic.com",
        "phoneNumber": "+911234567890",
        "fullAddress": "12 MG Road, Bangalore",
        "city": "Bangalore",
        "state": "Karnataka",
        "zipCode": "560001",
        "country": "India",
        "latitude": "12.9716",
        "longitude": "77.5946",
        "businessType": "physical",
        "shortTagline": "Your health is our priority",
    },
    {
        "merchant_email": "rahul@techacademy.com",
        "businessName": "Rahul Tech Academy",
        "businessDescription": "Online and offline tech courses for all levels",
        "primaryCategory": "Education",
        "businessEmail": "rahul@techacademy.com",
        "phoneNumber": "+919876543210",
        "fullAddress": "45 Anna Salai, Chennai",
        "city": "Chennai",
        "state": "Tamil Nadu",
        "zipCode": "600002",
        "country": "India",
        "latitude": "13.0827",
        "longitude": "80.2707",
        "businessType": "hybrid",
        "shortTagline": "Learn. Build. Grow.",
    },
    {
        "merchant_email": "anita@fitzone.com",
        "businessName": "FitZone Gym",
        "businessDescription": "State of the art gym with personal trainers",
        "primaryCategory": "Fitness",
        "businessEmail": "anita@fitzone.com",
        "phoneNumber": "+918765432109",
        "fullAddress": "78 Linking Road, Mumbai",
        "city": "Mumbai",
        "state": "Maharashtra",
        "zipCode": "400050",
        "country": "India",
        "latitude": "19.0760",
        "longitude": "72.8777",
        "businessType": "physical",
        "shortTagline": "Fit body, fit mind",
    },
]
for p in profiles:
    mid = merchant_ids.get(p["merchant_email"])
    if not mid:
        continue
    cur.execute("SELECT id FROM merchant_profiles WHERE merchant_id = %s", (mid,))
    if not cur.fetchone():
        pid = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO merchant_profiles (
                id, merchant_id, \"businessName\", \"businessDescription\", \"primaryCategory\",
                \"businessEmail\", \"phoneNumber\", \"fullAddress\", city, state, \"zipCode\", country,
                latitude, longitude, \"businessType\", \"shortTagline\", status
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            pid, mid, p["businessName"], p["businessDescription"], p["primaryCategory"],
            p["businessEmail"], p["phoneNumber"], p["fullAddress"], p["city"], p["state"],
            p["zipCode"], p["country"], p["latitude"], p["longitude"],
            p["businessType"], p["shortTagline"], "approved"
        ))
        print(f"✓ Profile: {p['businessName']}")

# ─── BUSINESSES ──────────────────────────────────────────
businesses = [
    ("priya@healthclinic.com", "Priya Health Clinic",  "Healthcare"),
    ("rahul@techacademy.com",  "Rahul Tech Academy",   "Education"),
    ("anita@fitzone.com",      "FitZone Gym",          "Fitness"),
]
business_ids = {}
for email, bname, cat in businesses:
    mid = merchant_ids.get(email)
    if not mid:
        continue
    cur.execute("SELECT id FROM businesses WHERE merchant_id = %s", (mid,))
    row = cur.fetchone()
    if not row:
        bid = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO businesses (id, name, category, status, merchant_id, is_deleted)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (bid, bname, cat, "approved", mid, False))
        business_ids[email] = bid
        print(f"✓ Business: {bname}")
    else:
        business_ids[email] = str(row[0])
        print(f"- Business exists: {bname}")

# ─── LISTINGS ────────────────────────────────────────────
listings = [
    {
        "email": "priya@healthclinic.com",
        "listingType": "service",
        "title": "General Health Checkup",
        "description": "Complete health checkup including blood tests and consultation",
        "price": 999.0,
        "category": "Healthcare",
    },
    {
        "email": "priya@healthclinic.com",
        "listingType": "service",
        "title": "Dental Consultation",
        "description": "Expert dental consultation and treatment",
        "price": 500.0,
        "category": "Healthcare",
    },
    {
        "email": "rahul@techacademy.com",
        "listingType": "training",
        "title": "Python Programming Course",
        "description": "Learn Python from scratch to advanced level",
        "price": 4999.0,
        "category": "Education",
    },
    {
        "email": "rahul@techacademy.com",
        "listingType": "training",
        "title": "Web Development Bootcamp",
        "description": "Full stack web development with React and FastAPI",
        "price": 9999.0,
        "category": "Education",
    },
    {
        "email": "anita@fitzone.com",
        "listingType": "service",
        "title": "Personal Training Session",
        "description": "One on one personal training with certified trainer",
        "price": 1500.0,
        "category": "Fitness",
    },
    {
        "email": "anita@fitzone.com",
        "listingType": "program",
        "title": "3 Month Fitness Program",
        "description": "Structured 3 month fitness program with diet plan",
        "price": 12000.0,
        "category": "Fitness",
    },
]
for l in listings:
    bid = business_ids.get(l["email"])
    cat_id = cat_ids.get(l["category"])
    if not bid:
        continue
    cur.execute("SELECT id FROM merchant_listings WHERE title = %s AND \"businessId\" = %s", (l["title"], bid))
    if not cur.fetchone():
        lid = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO merchant_listings (
                id, \"businessId\", \"listingType\", title, description,
                \"categoryId\", price, currency, status, images, tags
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            lid, bid, l["listingType"], l["title"], l["description"],
            cat_id, l["price"], "INR", "published", "{}", "{}"
        ))
        print(f"✓ Listing: {l['title']}")

# ─── CUSTOMERS ───────────────────────────────────────────
customers = [
    (str(uuid.uuid4()), "Amit",   "Kumar",   "amit@example.com",   "+911111111111"),
    (str(uuid.uuid4()), "Sneha",  "Reddy",   "sneha@example.com",  "+912222222222"),
    (str(uuid.uuid4()), "Vikram", "Singh",   "vikram@example.com", "+913333333333"),
]
for cid, fname, lname, email, mobile in customers:
    cur.execute("SELECT id FROM customers WHERE email = %s", (email,))
    if not cur.fetchone():
        cur.execute("""
            INSERT INTO customers (id, \"firstName\", \"lastName\", email, \"mobileNumber\",
                password, \"acceptTerms\", \"acceptPrivacyPolicy\", status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (cid, fname, lname, email, mobile, hash_password("Password@123"), True, True, "active"))
        print(f"✓ Customer: {fname} {lname}  →  {email} / Password@123")
    else:
        print(f"- Customer exists: {email}")

conn.commit()
cur.close()
conn.close()
print("\n✅ Seed complete!")
print("\n--- Login Credentials ---")
print("Admin:     admin@example.com     / admin123")
print("Merchant1: priya@healthclinic.com / Password@123")
print("Merchant2: rahul@techacademy.com  / Password@123")
print("Merchant3: anita@fitzone.com      / Password@123")
print("Customer1: amit@example.com       / Password@123")
print("Customer2: sneha@example.com      / Password@123")
print("Customer3: vikram@example.com     / Password@123")
