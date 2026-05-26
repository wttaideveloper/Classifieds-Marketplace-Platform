from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from uuid import UUID
from app.db.database import SessionLocal
from app.services.address_service import (
    add_address_service,
    get_addresses_service,
    update_address_service,
    delete_address_service
)
from app.schemas.customer_schema import AddressBase, AddressCreate, AddressResponse, AddressListResponse, AddressUpdate
from app.db.database import get_db

router = APIRouter()

@router.post("/", response_model=AddressResponse, status_code=status.HTTP_201_CREATED)
def add_address(
    payload: AddressCreate,
    customer_id: UUID,
    db: Session = Depends(get_db)
):
    return add_address_service(db, customer_id, payload.model_dump())


@router.get("/", response_model=AddressListResponse, status_code=status.HTTP_200_OK)
def get_addresses(
    customer_id: UUID,
    db: Session = Depends(get_db)
):
    return get_addresses_service(db, customer_id)


@router.put("/{address_id}", response_model=AddressResponse, status_code=status.HTTP_200_OK)
def update_address(
    address_id: UUID,
    payload: AddressUpdate,
    customer_id: UUID,
    db: Session = Depends(get_db)
):
    return update_address_service(db, address_id, customer_id, payload.model_dump())


@router.delete("/{address_id}", status_code=status.HTTP_200_OK)
def delete_address(
    address_id: UUID,
    customer_id: UUID,
    db: Session = Depends(get_db)
):
    return delete_address_service(db, address_id, customer_id)