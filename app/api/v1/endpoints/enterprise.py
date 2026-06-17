from uuid import UUID

from fastapi import APIRouter
from fastapi import Depends
from fastapi import status

from sqlalchemy.orm import Session

from app.db.database import get_db

from app.schemas.enterprise_schema import (
    EnterpriseCreate,
    EnterpriseUpdate,
    EnterpriseResponse
)

from app.services.enterprise_service import (
    create_enterprise_service,
    get_all_enterprises_service,
    get_enterprise_service,
    update_enterprise_service,
    delete_enterprise_service
)

router = APIRouter(
    tags=["Enterprise"]
)


@router.post(
    "",
    response_model=EnterpriseResponse,
    status_code=status.HTTP_201_CREATED
)
def create_enterprise(
        enterprise: EnterpriseCreate,
        db: Session = Depends(get_db)
):
    return create_enterprise_service(
        db,
        enterprise
    )


@router.get(
    "",
    response_model=list[EnterpriseResponse],
    status_code=status.HTTP_200_OK
)
def get_enterprises(
        db: Session = Depends(get_db)
):
    return get_all_enterprises_service(db)


@router.get(
    "/{enterprise_id}",
    response_model=EnterpriseResponse,
    status_code=status.HTTP_200_OK
)
def get_enterprise(
        enterprise_id: UUID,
        db: Session = Depends(get_db)
):
    return get_enterprise_service(
        db,
        enterprise_id
    )


@router.put(
    "/{enterprise_id}",
    response_model=EnterpriseResponse,
    status_code=status.HTTP_200_OK
)
def update_enterprise(
        enterprise_id: UUID,
        enterprise: EnterpriseUpdate,
        db: Session = Depends(get_db)
):
    return update_enterprise_service(
        db,
        enterprise_id,
        enterprise
    )


@router.delete(
    "/{enterprise_id}",
    status_code=status.HTTP_200_OK
)
def delete_enterprise(
        enterprise_id: UUID,
        db: Session = Depends(get_db)
):
    delete_enterprise_service(
        db,
        enterprise_id
    )

    return {
        "message":
        "Enterprise marked inactive successfully"
    }