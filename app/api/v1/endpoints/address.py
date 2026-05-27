from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.services.address_service import (
    add_address_service,
    get_addresses_service,
    update_address_service,
    delete_address_service
)
from app.schemas.customer_schema import AddressBase

router = APIRouter(dependencies=[Depends(get_current_user)])

@router.post("/")
def add_address(
    payload: AddressBase,
    customer_id: str,
    db: Session = Depends(get_db)
):
    return add_address_service(db, customer_id, payload.dict())


@router.get("/")
def get_addresses(
    customer_id: str,
    db: Session = Depends(get_db)
):
    return get_addresses_service(db, customer_id)


@router.put("/{address_id}")
def update_address(
    address_id: str,
    payload: AddressBase,
    customer_id: str,
    db: Session = Depends(get_db)
):
    return update_address_service(db, address_id, customer_id, payload.dict())


@router.delete("/{address_id}")
def delete_address(
    address_id: str,
    customer_id: str,
    db: Session = Depends(get_db)
):
    return delete_address_service(db, address_id, customer_id)
