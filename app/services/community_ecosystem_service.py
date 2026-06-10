from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from uuid import UUID

from app.repository.community_ecosystem_repo import (
    create_ecosystem_repo,
    get_by_id_ecosystem_repo,
    get_all_ecosystem_repo,
    delete_ecosystem_repo,
    update_ecosytem_repo
)


ALLOWED_PROVIDER_TYPES = [
    "clinic",
    "digital_clinic",
    "physician",
    "restaurant",
    "health_product_vendor",
    "wellness_organization",
    "hospital"
]


def create_ecosystem_service(
        db: Session,
        payload
    ):

        if payload.provider_type not in ALLOWED_PROVIDER_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid provider type"
            )

        return create_ecosystem_repo.create(
            db,
            payload.model_dump()
        )

   
def get_all_ecosystem_service(db: Session):
        return get_all_ecosystem_repo.get_all(db)


def get_by_id_ecosystem_service(
        db: Session,
        ecosystem_id: UUID
    ):

        ecosystem = (
            get_by_id_ecosystem_repo.get_by_id(
                db,
                ecosystem_id
            )
        )

        if not ecosystem:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider not found"
            )

        return ecosystem

   
def update_ecosystem_service(
        db: Session,
        ecosystem_id: UUID,
        payload
    ):

        ecosystem = (
            get_by_id_ecosystem_repo.get_by_id(
                db,
                ecosystem_id
            )
        )

        if not ecosystem:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider not found"
            )

        data = payload.model_dump(exclude_unset=True)

        if (
            "provider_type" in data
            and data["provider_type"]
            not in ALLOWED_PROVIDER_TYPES
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid provider type"
            )

        return update_ecosytem_repo.update(
            db,
            ecosystem,
            data
        )

def delete_ecosystem_service(
        db: Session,
        ecosystem_id: UUID
    ):

        ecosystem = (
            get_by_id_ecosystem_repo.get_by_id(
                db,
                ecosystem_id
            )
        )

        if not ecosystem:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider not found"
            )

        delete_ecosystem_repo.delete(
            db,
            ecosystem
        )

        return {
            "message": "Provider deleted successfully"
        }