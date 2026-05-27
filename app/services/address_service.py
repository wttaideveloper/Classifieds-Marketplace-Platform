# from uuid import uuid4
# from app.models.address_model import Address
# from app.exceptions.custom_exception import CustomException


# #  ADD ADDRESS
# def add_address_service(db, cust_id, data):

#     if data.get("is_default"):
#         db.query(Address).filter(Address.customer_id == cust_id).update(
#             {"is_default": False}
#         )
#         db.commit()

#     address = Address(
#         id=uuid4(),
#         customer_id=cust_id,
#         **data
#     )

#     db.add(address)
#     db.commit()
#     db.refresh(address)

#     return address

# #  GET ADDRESSES 
# def get_addresses_service(db, cust_id):

#     return db.query(Address).filter(
#         Address.customer_id == cust_id
#     ).all()

# # UPDATE ADDRESS 
# def update_address_service(db, address_id, cust_id, data):

#     address = db.query(Address).filter(
#         Address.id == address_id,
#         Address.customer_id == cust_id
#     ).first()

#     if not address:
#         raise CustomException(404, "Address not found")

#     allowed_fields = {
#         "address_line_1",
#         "address_line_2",
#         "city",
#         "state",
#         "zip_code",
#         "country",
#         "is_default"
#     }

#     for k, v in data.items():
#         if k in allowed_fields:
#             setattr(address, k, v)

#     db.commit()
#     db.refresh(address)

#     return address


# #  DELETE ADDRESS
# def delete_address_service(db, address_id, cust_id):

#     address = db.query(Address).filter(
#         Address.id == address_id,
#         Address.customer_id == cust_id
#     ).first()

#     if not address:
#         raise CustomException(404, "Address not found")

#     db.delete(address)
#     db.commit()

#     return {"message": "Deleted successfully"}

from uuid import UUID, uuid4

from app.models.address_model import Address
from app.exceptions.custom_exception import CustomException

from app.repository.address_repo import (
    create_address_repo,
    get_addresses_by_customer_id,
    get_customer_address_by_id,
    update_address_repo,
    delete_address_repo,
    unset_default_addresses,
    unset_other_default_addresses
)


# ADD ADDRESS
def add_address_service(
    db,
    customer_id: UUID,
    data: dict
):

    if data.get("is_default"):

        unset_default_addresses(
            db=db,
            customer_id=customer_id
        )

    address = Address(
        id=uuid4(),
        customer_id=customer_id,
        **data
    )

    return create_address_repo(
        db=db,
        address=address
    )


# GET ALL ADDRESSES
def get_addresses_service(
    db,
    customer_id: UUID
):

    return get_addresses_by_customer_id(
        db=db,
        customer_id=customer_id
    )


# UPDATE ADDRESS
def update_address_service(
    db,
    address_id: UUID,
    customer_id: UUID,
    data: dict
):

    address = get_customer_address_by_id(
        db=db,
        address_id=address_id,
        customer_id=customer_id
    )

    if not address:

        raise CustomException(
            404,
            "Address not found"
        )

    if data.get("is_default"):

        unset_other_default_addresses(
            db=db,
            customer_id=customer_id,
            address_id=address_id
        )

    updated_address = update_address_repo(
        db=db,
        address=address,
        data=data
    )

    return updated_address


# DELETE ADDRESS
def delete_address_service(
    db,
    address_id: UUID,
    customer_id: UUID
):

    address = get_customer_address_by_id(
        db=db,
        address_id=address_id,
        customer_id=customer_id
    )

    if not address:

        raise CustomException(
            404,
            "Address not found"
        )

    delete_address_repo(
        db=db,
        address=address
    )

    return {
        "success": True,
        "message": "Address deleted successfully"
    }