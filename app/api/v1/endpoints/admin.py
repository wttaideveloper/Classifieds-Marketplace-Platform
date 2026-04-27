from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.admin_schema import AdminLogin
from app.services.admin_service import admin_login_service

router = APIRouter(tags=["Admin"])

@router.post("/login", status_code=status.HTTP_200_OK)
def admin_login(
    payload: AdminLogin,
    db: Session = Depends(get_db)
):
    return admin_login_service(db, payload)