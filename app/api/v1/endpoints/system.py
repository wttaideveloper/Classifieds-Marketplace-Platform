from fastapi import APIRouter
from fastapi import Depends
from fastapi import status

from sqlalchemy.orm import Session

from app.db.database import get_db

from app.services.system_service import (
    health_check_service,
    inventory_service
)

router = APIRouter(
    tags=["System"]
)


@router.get(
    "/health",
    status_code=status.HTTP_200_OK
)
def health_check(
    db: Session = Depends(get_db)
):
    return health_check_service(db)


@router.get(
    "/inventory",
    status_code=status.HTTP_200_OK
)
def inventory():
    return inventory_service()