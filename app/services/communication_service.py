from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.repository.communication_repo import (
    create_communication,
    get_all_communications,
    get_communication_by_id,
    update_communication,
    delete_communication
)

def create_communication_service(
        db: Session,
        payload
):
    try:
        return create_communication(
            db,
            payload
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


def get_all_communications_service(
        db: Session
):
    return get_all_communications(db)


def get_communication_service(
        db: Session,
        communication_id: UUID
):
    communication = get_communication_by_id(
        db,
        communication_id
    )

    if not communication:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Communication not found"
        )

    return communication

def update_communication_service(
        db: Session,
        communication_id: UUID,
        payload
):
    communication = get_communication_by_id(
        db,
        communication_id
    )

    if not communication:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Communication not found"
        )

    update_data = payload.dict(
        exclude_unset=True
    )

    for key, value in update_data.items():
        setattr(
            communication,
            key,
            value
        )

    return update_communication(
        db,
        communication
    )

def delete_communication_service(
        db: Session,
        communication_id: UUID
):
    communication = get_communication_by_id(
        db,
        communication_id
    )

    if not communication:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Communication not found"
        )

    delete_communication(
        db,
        communication
    )

    return {
        "message":
        "Communication deleted successfully"
    }