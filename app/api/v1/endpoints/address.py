from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.database import SessionLocal
from app.services.address_service import (
    add_address_service,
    get_addresses_service,
    update_address_service,
    delete_address_service
)
from app.schemas.customer_schema import AddressBase

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("")
def add_address(payload: AddressBase, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    return add_address_service(db, current_user["id"], payload.dict())


@router.get("")
def get_addresses(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    return get_addresses_service(db, current_user["id"])


@router.put("/{address_id}")
def update_address(
    address_id: str,
    payload: AddressBase,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return update_address_service(db, address_id, current_user["id"], payload.dict())


@router.delete("/{address_id}")
def delete_address(
    address_id: str,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return delete_address_service(db, address_id, current_user["id"])