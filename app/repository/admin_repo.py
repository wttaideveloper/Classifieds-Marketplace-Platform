# app/repository/admin_repo.py
from sqlalchemy.orm import Session, joinedload
from app.models.admin_model import Admin, Business
from datetime import datetime

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

def get_all_businesses(
    db: Session,
    search: str = None,
    status: str = None,
    category: str = None,
    skip: int = 0,
    limit: int = 10
):
    query = db.query(Business).filter(Business.is_deleted == False)

    if search:
        query = query.filter(
            Business.name.ilike(f"%{search}%")
        )

    if status:
        query = query.filter(Business.status == status)

    if category:
        query = query.filter(Business.category == category)

    total = query.count()

    businesses = query.order_by(Business.created_at.desc()) \
                      .offset(skip) \
                      .limit(limit) \
                      .all()

    return total, businesses

def get_business_by_id(db: Session, business_id):
    return db.query(Business).filter(
        Business.id == business_id,
        Business.is_deleted == False
    ).first()

def approve_business(db: Session, business):
    business.status = "approved"
    business.approved_at = datetime.utcnow()

    db.commit()
    db.refresh(business)

    return business

def reject_business(db: Session, business, reason: str = None):
    business.status = "rejected"
    business.rejection_reason = reason
    business.rejected_at = datetime.utcnow()

    db.commit()
    db.refresh(business)

    return business

def suspend_business(db: Session, business, reason: str = None):
    business.status = "suspended"
    business.suspension_reason = reason
    business.suspended_at = datetime.utcnow()

    db.commit()
    db.refresh(business)

    return business

def reactivate_business(db: Session, business):
    business.status = "approved"
    business.suspension_reason = None
    business.suspended_at = None

    db.commit()
    db.refresh(business)

    return business

def get_business_with_merchant(db: Session, business_id):
    return db.query(Business).options(
        joinedload(Business.merchant)
    ).filter(
        Business.id == business_id,
        Business.is_deleted == False
    ).first()