from uuid import UUID

from fastapi import HTTPException
from fastapi import status

from sqlalchemy.orm import Session

from app.models.enterprise_model import Enterprise

from app.repository.service_repo import (
    create_service,
    get_services,
    get_service_by_id,
    update_service,
    delete_service
)


def create_service_service(
    db: Session,
    service_data
):
    enterprise = (
        db.query(Enterprise)
        .filter(
            Enterprise.id ==
            service_data.enterprise_id
        )
        .first()
    )

    if not enterprise:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enterprise not found"
        )

    return create_service(
        db,
        service_data
    )


def get_services_service(
    db: Session
):
    return get_services(db)


def get_service_service(
    db: Session,
    service_id: UUID
):
    service = get_service_by_id(
        db,
        service_id
    )

    if not service:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service not found"
        )

    return service


def update_service_service(
    db: Session,
    service_id: UUID,
    update_data
):
    service = get_service_by_id(
        db,
        service_id
    )

    if not service:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service not found"
        )

    return update_service(
        db,
        service,
        update_data
    )


def delete_service_service(
    db: Session,
    service_id: UUID
):
    service = get_service_by_id(
        db,
        service_id
    )

    if not service:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service not found"
        )

    return delete_service(
        db,
        service
    )