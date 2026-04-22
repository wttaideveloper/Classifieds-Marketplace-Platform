from uuid import uuid4
from app.models.address_model import Address
from app.repository.address_repo import *
from app.exceptions.custom_exception import CustomException


def add_address_service(db, cust_id, data):

    if data.get("isDefault"):
        db.query(Address).filter(Address.customerId == cust_id).update(
            {"isDefault": False}
        )

    address = Address(
        id=str(uuid4()),
        customerId=cust_id,
        **data
    )

    db.add(address)
    db.commit()
    db.refresh(address)

    return address


def get_addresses_service(db, cust_id):

    return db.query(Address).filter(Address.customerId == cust_id).all()


def update_address_service(db, address_id, cust_id, data):

    address = db.query(Address).filter(
        Address.id == address_id,
        Address.customerId == cust_id
    ).first()

    if not address:
        raise CustomException(404, "Address not found")

    for k, v in data.items():
        setattr(address, k, v)

    db.commit()
    db.refresh(address)

    return address


def delete_address_service(db, address_id, cust_id):

    address = db.query(Address).filter(
        Address.id == address_id,
        Address.customerId == cust_id
    ).first()

    if not address:
        raise CustomException(404, "Address not found")

    db.delete(address)
    db.commit()

    return {"message": "Deleted successfully"}