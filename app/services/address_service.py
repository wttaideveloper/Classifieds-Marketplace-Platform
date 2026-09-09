from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.repository.address_repo import create_address, get_addresses
from app.schemas.address_schema import AddressCreate, AddressListResponse, AddressResponse


def _user_uuid(current_user: dict) -> UUID:
    raw = current_user.get("id") if current_user else None
    if not raw:
        raise HTTPException(status_code=400, detail="User id not found in token")
    try:
        return UUID(str(raw))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user id in token")


def get_addresses_service(db: Session, current_user: dict) -> AddressListResponse:
    user_id = _user_uuid(current_user)
    rows = get_addresses(db, user_id)
    return AddressListResponse(items=[AddressResponse.model_validate(row) for row in rows])


def create_address_service(db: Session, current_user: dict, payload: AddressCreate) -> AddressResponse:
    user_id = _user_uuid(current_user)
    obj = create_address(db, user_id, payload.model_dump())
    return AddressResponse.model_validate(obj)
