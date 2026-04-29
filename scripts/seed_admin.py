from app.db.database import SessionLocal
from app.models.admin_model import Admin
from app.core.security import hash_password

db = SessionLocal()

existing = db.query(Admin).filter(Admin.email == "admin@example.com").first()

if not existing:
    admin = Admin(
        name="Super Admin",
        email="rs5804@srmist.edu.in",
        password=hash_password("admin123")
    )
    db.add(admin)
    db.commit()
    print("Admin created")
else:
    print("Admin already exists")

db.close()