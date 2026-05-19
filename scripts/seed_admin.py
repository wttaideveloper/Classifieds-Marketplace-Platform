import sys
sys.path.insert(0, '.')

# Import all models first so SQLAlchemy relationships resolve correctly
import app.models.merchant_model
import app.models.customer_model
import app.models.admin_model
import app.models.blog_model
import app.models.category_model

from app.db.database import SessionLocal
from app.models.admin_model import Admin
from app.core.security import hash_password

db = SessionLocal()

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "admin123"

existing = db.query(Admin).filter(Admin.email == ADMIN_EMAIL).first()

if not existing:
    admin = Admin(
        name="Super Admin",
        email=ADMIN_EMAIL,
        password=hash_password(ADMIN_PASSWORD)
    )
    db.add(admin)
    db.commit()
    print("Admin created")
else:
    print("Admin already exists")

db.close()