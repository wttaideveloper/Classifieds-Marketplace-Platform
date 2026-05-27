import os

from app.db.database import SessionLocal
from app.models.admin_model import Admin
from app.core.security import hash_password

admin_name = os.getenv("SEED_ADMIN_NAME", "Super Admin")
admin_email = os.getenv("SEED_ADMIN_EMAIL")
admin_password = os.getenv("SEED_ADMIN_PASSWORD")

if not admin_email or not admin_password:
    raise RuntimeError(
        "Set SEED_ADMIN_EMAIL and SEED_ADMIN_PASSWORD before running seed_admin.py"
    )

db = SessionLocal()

existing = db.query(Admin).filter(Admin.email == admin_email).first()

if not existing:
    admin = Admin(
        name=admin_name,
        email=admin_email,
        password=hash_password(admin_password)
    )
    db.add(admin)
    db.commit()
    print("Admin created")
else:
    print("Admin already exists")

db.close()
