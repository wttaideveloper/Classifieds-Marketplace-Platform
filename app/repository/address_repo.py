from sqlalchemy.orm import Session
from app.models.address_model import Address


# -------------------------
# CREATE
# -------------------------
def create_address_repo(db: Session, address: Address):
    db.add(address)
    db.commit()
    db.refresh(address)
    return address


# -------------------------
# READ ALL BY CUSTOMER
# -------------------------
def get_addresses_by_customer_id(db: Session, customer_id: int):
    return (
        db.query(Address)
        .filter(Address.customer_id == customer_id)
        .all()
    )


# -------------------------
# READ ONE
# -------------------------
def get_address_by_id(db: Session, address_id: int):
    return (
        db.query(Address)
        .filter(Address.id == address_id)
        .first()
    )


# -------------------------
# UPDATE
# -------------------------
def update_address(db: Session, address_id: int, data: dict):

    address = get_address_by_id(db, address_id)

    if not address:
        return None

    for key, value in data.items():
        setattr(address, key, value)

    db.commit()
    db.refresh(address)
    return address


# -------------------------
# DELETE
# -------------------------
def delete_address(db: Session, address_id: int):

    address = get_address_by_id(db, address_id)

    if not address:
        return None

    db.delete(address)
    db.commit()
    return address


# -------------------------
# UNSET DEFAULT ADDRESSES
# -------------------------
def unset_default_addresses(db: Session, customer_id: int):

    db.query(Address)\
      .filter(Address.customer_id == customer_id)\
      .update(
          {"isDefault": False},
          synchronize_session=False
      )

    db.commit()