from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from app.db.database import SessionLocal
from app.schemas.merchant_schema import MerchantRegister, MerchantLogin
from app.services.merchant_service import register_merchant_service, login_merchant_service, google_login_service

router = APIRouter(
    tags=["Merchant Auth"]
)

# DB Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_merchant(user: MerchantRegister, db: Session = Depends(get_db)):
    return register_merchant_service(db, user)

@router.post("/login", status_code=status.HTTP_200_OK)
def login_merchant(user: MerchantLogin, db: Session = Depends(get_db)):
    return login_merchant_service(db, user.email, user.password)

@router.post("/google")
def google_login(payload: dict, db: Session = Depends(get_db)):
    return google_login_service(db, payload.get("googleToken"))