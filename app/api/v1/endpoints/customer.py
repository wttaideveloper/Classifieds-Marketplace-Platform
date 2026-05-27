from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.schemas.customer_schema import CustomerRegister, CustomerLogin
from app.services.customer_service import (
    register_customer_service,
    login_customer_service,
    google_login_service
)

router = APIRouter()

#  REGISTER
@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(user: CustomerRegister, db: Session = Depends(get_db)):
    return register_customer_service(db, user)

# LOGIN
@router.post("/login")
def login(user: CustomerLogin, db: Session = Depends(get_db)):
    return login_customer_service(db, user.email, user.password)

# GOOGLE LOGIN
@router.post("/google")
def google_login(payload: dict, db: Session = Depends(get_db)):
    return google_login_service(db, payload.get("googleToken"))
