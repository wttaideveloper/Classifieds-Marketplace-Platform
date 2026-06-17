from uuid import UUID

from fastapi import HTTPException
from fastapi import status

from sqlalchemy.orm import Session

from app.repository.enterprise_repo import (
    create_enterprise,
    get_enterprises,
    get_enterprise_by_id,
    update_enterprise,
    delete_enterprise
)


def create_enterprise_service(
        db: Session,
        enterprise_data
):
    existing = next(
        (
            e for e in get_enterprises(db)
            if e.business_email ==
            enterprise_data.business_email
        ),
        None
    )

    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Business email already exists"
        )

    return create_enterprise(
        db,
        enterprise_data
    )


def get_all_enterprises_service(
        db: Session
):
    return get_enterprises(db)


def get_enterprise_service(
        db: Session,
        enterprise_id: UUID
):
    enterprise = get_enterprise_by_id(
        db,
        enterprise_id
    )

    if not enterprise:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enterprise not found"
        )

    return enterprise


def update_enterprise_service(
        db: Session,
        enterprise_id: UUID,
        update_data
):
    enterprise = get_enterprise_by_id(
        db,
        enterprise_id
    )

    if not enterprise:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enterprise not found"
        )

    return update_enterprise(
        db,
        enterprise,
        update_data
    )


def delete_enterprise_service(
        db: Session,
        enterprise_id: UUID
):
    enterprise = get_enterprise_by_id(
        db,
        enterprise_id
    )

    if not enterprise:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enterprise not found"
        )

    return delete_enterprise(
        db,
        enterprise
    )