from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from uuid import UUID

from app.db.database import get_db

from app.schemas.enterprise_setup_schema import (
    EnterpriseSetupCreate,
    EnterpriseSetupUpdate,
    EnterpriseSetupResponse
)

from app.services.enterprise_setup_service import (
    create_enterprise_service,
    get_all_enterprises_service,
    get_enterprise_by_id_service,
    update_enterprise_service,
    delete_enterprise_service
)

router = APIRouter(
    tags=["Enterprise Setup"]
)


@router.post(
    "/",
    response_model=EnterpriseSetupResponse,
    status_code=status.HTTP_201_CREATED
)
def create_enterprise(
    payload: EnterpriseSetupCreate,
    db: Session = Depends(get_db)
):
    return create_enterprise_service(
        db,
        payload
    )


@router.get(
    "/",
    response_model=list[EnterpriseSetupResponse]
)
def get_all_enterprises(
    db: Session = Depends(get_db)
):
    return get_all_enterprises_service(db)


@router.get(
    "/{enterprise_id}",
    response_model=EnterpriseSetupResponse
)
def get_enterprise(
    enterprise_id: UUID,
    db: Session = Depends(get_db)
):
    return get_enterprise_by_id_service(
        db,
        enterprise_id
    )


@router.put(
    "/{enterprise_id}",
    response_model=EnterpriseSetupResponse
)
def update_enterprise(
    enterprise_id: UUID,
    payload: EnterpriseSetupUpdate,
    db: Session = Depends(get_db)
):
    return update_enterprise_service(
        db,
        enterprise_id,
        payload
    )


@router.delete(
    "/{enterprise_id}",
    status_code=status.HTTP_200_OK
)
def delete_enterprise(
    enterprise_id: UUID,
    db: Session = Depends(get_db)
):
    return delete_enterprise_service(
        db,
        enterprise_id
    )