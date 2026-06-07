from uuid import UUID
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.models.address_model import Address


def create_address_repo(
    db: Session,
    address: Address
):

    try:

        db.add(address)
        db.commit()
        db.refresh(address)

        return address

    except SQLAlchemyError:

        db.rollback()
        raise


def get_addresses_by_customer_id(
    db: Session,
    customer_id: UUID
):

    return (
        db.query(Address)
        .filter(Address.customer_id == customer_id)
        .all()
    )


def get_address_by_id(
    db: Session,
    address_id: UUID
):

    return (
        db.query(Address)
        .filter(Address.id == address_id)
        .first()
    )


def get_customer_address_by_id(
    db: Session,
    address_id: UUID,
    customer_id: UUID
):

    return (
        db.query(Address)
        .filter(
            Address.id == address_id,
            Address.customer_id == customer_id
        )
        .first()
    )


def update_address_repo(
    db: Session,
    address: Address,
    data: dict
):

    try:

        for key, value in data.items():
            setattr(address, key, value)

        db.commit()
        db.refresh(address)

        return address

    except SQLAlchemyError:

        db.rollback()
        raise


def delete_address_repo(
    db: Session,
    address: Address
):

    try:

        db.delete(address)
        db.commit()

        return address

    except SQLAlchemyError:

        db.rollback()
        raise


def unset_default_addresses(
    db: Session,
    customer_id: UUID
):

    try:

        db.query(Address).filter(
            Address.customer_id == customer_id
        ).update(
            {"is_default": False},
            synchronize_session=False
        )

        db.commit()

    except SQLAlchemyError:

        db.rollback()
        raise


def unset_other_default_addresses(
    db: Session,
    customer_id: UUID,
    address_id: UUID
):

    try:

        db.query(Address).filter(
            Address.customer_id == customer_id,
            Address.id != address_id
        ).update(
            {"is_default": False},
            synchronize_session=False
        )

        db.commit()

    except SQLAlchemyError:

        db.rollback()
        raise