# app/repository/admin_repo.py
from sqlalchemy.orm import Session
from app.models.admin_model import Admin

def get_admin_by_id(db: Session, admin_id: int):
    return db.query(Admin).filter(Admin.id == admin_id).first()

def get_admin_by_email(db: Session, email: str):
    return db.query(Admin).filter(Admin.email == email).first()


def get_admin_by_reset_token(db: Session, token: str):
    return db.query(Admin).filter(Admin.resetToken == token).first()

def update_admin(db: Session, admin):
    db.commit()
    db.refresh(admin)
    return admin