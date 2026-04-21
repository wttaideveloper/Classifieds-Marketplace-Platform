from fastapi import APIRouter, status, Depends
from app.schemas.user_schema import UserRegister, UserLogin
from app.services.user_service import *
from app.core.dependencies import get_current_user, get_current_admin

router = APIRouter()


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(user: UserRegister):
    return register_user_service(user)


@router.post("/login", status_code=status.HTTP_200_OK)
def login(user: UserLogin):
    return login_user_service(user)


@router.get("/profile")
def get_profile(current_user=Depends(get_current_user)):
    return {
        "message": "User profile fetched successfully",
        "user": {
            "id": current_user["id"],
            "username": current_user["username"],
            "email": current_user["email"]
        }
    }
@router.get("/admin")
def admin_panel(user=Depends(get_current_admin)):
    return {"message": "Welcome Admin"}