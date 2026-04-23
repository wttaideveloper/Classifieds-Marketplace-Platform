from sqlalchemy.orm import Session
from app.models.merchant_model import Merchant

def get_merchant_by_email(db: Session, email: str):
    return db.query(Merchant).filter(Merchant.businessEmail == email).first()

def get_merchant_by_id(db: Session, merch_id: str):
    return db.query(Merchant).filter(Merchant.id == merch_id).first()

def create_merchant(db: Session, merchant: Merchant):
    db.add(merchant)
    db.commit()
    db.refresh(merchant)
    return merchant

def update_merchant(db: Session, merchant: Merchant):
    db.commit()
    db.refresh(merchant)
    return merchant