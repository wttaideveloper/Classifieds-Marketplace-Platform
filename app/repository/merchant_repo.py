from sqlalchemy.orm import Session
from app.models.merchant_model import (
    Merchant,
    MerchantProfile,
    MerchantBusinessDraft
)

# MERCHANT 

def get_merchant_by_email(db: Session, email: str):
    return db.query(Merchant).filter(
        Merchant.businessEmail == email
    ).first()

def get_merchant_by_id(db: Session, merch_id: str):
    return db.query(Merchant).filter(
        Merchant.id == merch_id
    ).first()

def create_merchant(db: Session, merchant: Merchant):
    try:
        db.add(merchant)
        db.commit()
        db.refresh(merchant)
        return merchant
    except Exception:
        db.rollback()
        raise

def update_merchant(db: Session, merchant: Merchant):
    try:
        db.add(merchant)
        db.commit()
        db.refresh(merchant)
        return merchant
    except Exception:
        db.rollback()
        raise

# BUSINESS PROFILE 

def get_business_profile_by_merchant_id(
    db: Session,
    merchant_id: str
):
    return db.query(MerchantProfile).filter(
        MerchantProfile.merchant_id == merchant_id
    ).first()

def create_business_profile(
    db: Session,
    profile: MerchantProfile
):
    try:
        db.add(profile)
        db.commit()
        db.refresh(profile)
        return profile
    except Exception:
        db.rollback()
        raise

def update_business_profile(
    db: Session,
    profile: MerchantProfile
):
    try:
        db.add(profile)
        db.commit()
        db.refresh(profile)
        return profile
    except Exception:
        db.rollback()
        raise

# BUSINESS DRAFT

def create_business_draft(
    db: Session,
    draft: MerchantBusinessDraft
):
    try:
        db.add(draft)
        db.commit()
        db.refresh(draft)
        return draft
    except Exception:
        db.rollback()
        raise

def update_business_profile(
    db: Session,
    profile: MerchantProfile
):
    try:
        db.add(profile)
        db.commit()
        db.refresh(profile)
        return profile
    except Exception:
        db.rollback()
        raise

