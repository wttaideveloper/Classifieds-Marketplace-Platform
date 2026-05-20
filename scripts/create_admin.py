import psycopg2
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

conn = psycopg2.connect(
    dbname="wisdomdatabase",
    user="postgres",
    password="fazil2004",
    host="localhost",
    port="5432"
)

cur = conn.cursor()

email = "admin@example.com"
password = pwd_context.hash("admin123")
name = "Super Admin"

cur.execute("SELECT id FROM admins WHERE email = %s", (email,))
existing = cur.fetchone()

if not existing:
    cur.execute(
        "INSERT INTO admins (name, email, password, status) VALUES (%s, %s, %s, %s)",
        (name, email, password, "active")
    )
    conn.commit()
    print("Admin created successfully")
    print(f"Email: {email}")
    print(f"Password: admin123")
else:
    print("Admin already exists")

cur.close()
conn.close()
