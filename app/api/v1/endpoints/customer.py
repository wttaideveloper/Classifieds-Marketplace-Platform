from fastapi import APIRouter, status, Depends
from app.schemas.customer_schema import CustomerRegister, CustomerLogin, GoogleLogin
from app.services.customer_service import *
from app.core.dependencies import get_current_user, get_current_admin

router = APIRouter()

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_customer(user: CustomerRegister):
    return register_customer_service(user)

@router.post("/login", status_code=status.HTTP_200_OK)
def login_customer(user: CustomerLogin):
    return login_customer_service(user)

@router.post("/google")
def google_login(data: GoogleLogin):
    return google_login_service(data)

@router.get("/admin")
def admin_panel(user=Depends(get_current_admin)):
    return {"message": "Welcome Admin"}