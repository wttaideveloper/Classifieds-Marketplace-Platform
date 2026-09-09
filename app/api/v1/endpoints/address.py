from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.schemas.address_schema import AddressCreate, AddressListResponse, AddressResponse
from app.services.address_service import create_address_service, get_addresses_service

router = APIRouter(tags=["Addresses"])


@router.get("/", response_model=AddressListResponse, summary="Get saved addresses")
def list_addresses(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return get_addresses_service(db, current_user)


@router.post(
    "/",
    response_model=AddressResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create new address",
)
def create_address(
    payload: AddressCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return create_address_service(db, current_user, payload)
