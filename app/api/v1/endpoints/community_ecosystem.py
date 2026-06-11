from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from uuid import UUID

from app.db.database import get_db

from app.schemas.community_ecosystem_schema import (
    CommunityEcosystemCreate,
    CommunityEcosystemUpdate,
    CommunityEcosystemResponse
)

from app.services.community_ecosystem_service import (
    create_ecosystem_service,
    get_all_ecosystem_service,
    get_by_id_ecosystem_service,
    update_ecosystem_service,
    delete_ecosystem_service

)

router = APIRouter(
    tags=["Community Ecosystem"]
)


@router.post(
    "/",
    response_model=CommunityEcosystemResponse,
    status_code=status.HTTP_201_CREATED
)
def create_provider(
    payload: CommunityEcosystemCreate,
    db: Session = Depends(get_db)
):
    return create_ecosystem_service(
        db,
        payload
    )

@router.get(
    "/",
    response_model=list[CommunityEcosystemResponse]
)
def get_all_providers(
    db: Session = Depends(get_db)
):
    return get_all_ecosystem_service(db)


@router.get(
    "/{ecosystem_id}",
    response_model=CommunityEcosystemResponse
)
def get_provider(
    ecosystem_id: UUID,
    db: Session = Depends(get_db)
):
    return get_by_id_ecosystem_service(
        db,
        ecosystem_id
    )

@router.put(
    "/{ecosystem_id}",
    response_model=CommunityEcosystemResponse
)
def update_provider(
    ecosystem_id: UUID,
    payload: CommunityEcosystemUpdate,
    db: Session = Depends(get_db)
):
    return update_ecosystem_service(
        db,
        ecosystem_id,
        payload
    )


@router.delete(
    "/{ecosystem_id}",
    status_code=status.HTTP_200_OK
)
def delete_provider(
    ecosystem_id: UUID,
    db: Session = Depends(get_db)
):
    return delete_ecosystem_service(
        db,
        ecosystem_id
    )