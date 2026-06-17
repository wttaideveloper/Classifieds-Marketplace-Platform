from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    status
)

from sqlalchemy.orm import Session

from app.db.database import get_db

from app.schemas.service_schema import (
    ServiceCreate,
    ServiceUpdate,
    ServiceResponse
)

from app.services.service_service import (
    create_service_service,
    get_services_service,
    get_service_service,
    update_service_service,
    delete_service_service
)

router = APIRouter(
    tags=["Services"]
)


@router.post(
    "/",
    response_model=ServiceResponse,
    status_code=status.HTTP_201_CREATED
)
def create_service(
    service: ServiceCreate,
    db: Session = Depends(get_db)
):
    return create_service_service(
        db,
        service
    )


@router.get(
    "/",
    response_model=list[ServiceResponse],
    status_code=status.HTTP_200_OK
)
def get_services(
    db: Session = Depends(get_db)
):
    return get_services_service(db)


@router.get(
    "/{service_id}",
    response_model=ServiceResponse,
    status_code=status.HTTP_200_OK
)
def get_service(
    service_id: UUID,
    db: Session = Depends(get_db)
):
    return get_service_service(
        db,
        service_id
    )


@router.put(
    "/{service_id}",
    response_model=ServiceResponse,
    status_code=status.HTTP_200_OK
)
def update_service(
    service_id: UUID,
    service: ServiceUpdate,
    db: Session = Depends(get_db)
):
    return update_service_service(
        db,
        service_id,
        service
    )


@router.delete(
    "/{service_id}",
    status_code=status.HTTP_200_OK
)
def delete_service(
    service_id: UUID,
    db: Session = Depends(get_db)
):
    delete_service_service(
        db,
        service_id
    )

    return {
        "message":
        "Service marked inactive successfully"
    }