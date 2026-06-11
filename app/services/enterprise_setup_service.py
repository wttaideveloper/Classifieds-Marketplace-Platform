from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from uuid import UUID
from app.models.enterprise_setup_model import EnterpriseSetup
from app.repository.enterprise_setup_repo import (
    create_enterprise_repo,
    get_all_enterprises_repo,
    get_enterprise_by_id_repo,
    update_enterprise_repo,
    delete_enterprise_repo
)


def create_enterprise_service(
    db: Session,
    data
):
    existing = (
        db.query(EnterpriseSetup)
        .filter_by(
            organization_code=data.organization_code
        )
        .first()
    )

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Organization code already exists"
        )

    return create_enterprise_repo(db, data)


def get_all_enterprises_service(
    db: Session
):
    return get_all_enterprises_repo(db)


def get_enterprise_by_id_service(
    db: Session,
    enterprise_id: UUID
):
    enterprise = get_enterprise_by_id_repo(
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
    enterprise = get_enterprise_by_id_repo(
        db,
        enterprise_id
    )

    if not enterprise:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enterprise not found"
        )

    return update_enterprise_repo(
        db,
        enterprise,
        update_data
    )


def delete_enterprise_service(
    db: Session,
    enterprise_id: UUID
):
    enterprise = get_enterprise_by_id_repo(
        db,
        enterprise_id
    )

    if not enterprise:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enterprise not found"
        )

    delete_enterprise_repo(
        db,
        enterprise
    )

    return {
        "message": "Enterprise deleted successfully"
    }