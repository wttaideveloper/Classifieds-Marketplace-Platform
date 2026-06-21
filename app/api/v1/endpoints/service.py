from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Path,
    status
)

from sqlalchemy.orm import Session

from app.db.database import get_db

from app.schemas.service_schema import (
    ServiceCreate,
    ServiceUpdate,
    ServiceDetailResponse,
    ServiceListItemResponse,
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
    status_code=status.HTTP_201_CREATED,
    summary="Create Service",
    description="""
Create a new service in the marketplace.

Services represent offerings provided by an enterprise.

Examples:
- Website Development
- Cloud Hosting
- Technical Support
- Digital Marketing
""",
    responses={
        201: {"description": "Service created successfully"},
        400: {"description": "Invalid request"},
        422: {"description": "Validation error"},
        500: {"description": "Internal server error"}
    }
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
    response_model=list[ServiceListItemResponse],
    status_code=status.HTTP_200_OK,
    summary="Get All Services",
    description="""
Retrieve all active services available in the system.
""",
    responses={
        200: {"description": "Services retrieved successfully"},
        500: {"description": "Internal server error"}
    }
)
def get_services(
    db: Session = Depends(get_db)
):
    return get_services_service(db)


@router.get(
    "/{service_id}",
    response_model=ServiceDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Service By ID",
    description="""
Retrieve a specific service using its unique identifier.
""",
    responses={
        200: {"description": "Service retrieved successfully"},
        404: {"description": "Service not found"},
        422: {"description": "Validation error"},
        500: {"description": "Internal server error"}
    }
)
def get_service(
    service_id: UUID = Path(
        ...,
        description="Unique identifier of the service"
    ),
    db: Session = Depends(get_db)
):
    return get_service_service(
        db,
        service_id
    )


@router.put(
    "/{service_id}",
    response_model=ServiceResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Service",
    description="""
Update an existing service.

Only the supplied fields will be updated.
""",
    responses={
        200: {"description": "Service updated successfully"},
        404: {"description": "Service not found"},
        422: {"description": "Validation error"},
        500: {"description": "Internal server error"}
    }
)
def update_service(
    service: ServiceUpdate,
    service_id: UUID = Path(
        ...,
        description="Unique identifier of the service"
    ),
    db: Session = Depends(get_db)
):
    return update_service_service(
        db,
        service_id,
        service
    )


@router.delete(
    "/{service_id}",
    status_code=status.HTTP_200_OK,
    summary="Deactivate Service",
    description="""
Marks a service as inactive.

This operation performs a soft delete and does not permanently remove the record.
""",
    responses={
        200: {"description": "Service marked inactive successfully"},
        404: {"description": "Service not found"},
        422: {"description": "Validation error"},
        500: {"description": "Internal server error"}
    }
)
def delete_service(
    service_id: UUID = Path(
        ...,
        description="Unique identifier of the service"
    ),
    db: Session = Depends(get_db)
):
    delete_service_service(
        db,
        service_id
    )

    return {
        "message": "Service marked inactive successfully"
    }