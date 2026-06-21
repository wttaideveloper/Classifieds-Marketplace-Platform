from uuid import UUID

from fastapi import (
    APIRouter,
    Body,
    Depends,
    Path,
    status
)
from sqlalchemy.orm import Session

from app.db.database import get_db

from app.schemas.enterprise_schema import (
    EnterpriseCreate,
    EnterpriseUpdate,
    EnterpriseDetailResponse,
    EnterpriseListItemResponse,
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
    "/",
    response_model=EnterpriseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Enterprise",
    description="""
Create a new enterprise.

An enterprise is the top-level business entity within the marketplace system.

Example:
- ABC Pvt Ltd
- XYZ Corporation
- Demo Enterprise
""",
    responses={
        201: {"description": "Enterprise created successfully"},
        400: {"description": "Invalid request"},
        422: {"description": "Validation error"},
        500: {"description": "Internal server error"}
    }
)
def create_enterprise(
    enterprise: EnterpriseCreate = Body(
        ...,
        openapi_examples={
            "required_fields": {
                "summary": "Minimum required fields",
                "value": {
                    "business_short_name": "Spin Health",
                    "business_legal_name": "Spin Health Co Pvt Ltd",
                    "business_email": "contact@spinhealth.com",
                },
            },
            "with_description": {
                "summary": "With business description",
                "value": {
                    "business_short_name": "Spin Health",
                    "business_legal_name": "Spin Health Co Pvt Ltd",
                    "business_description": (
                        "We provide Top-Class fitness programs for kids"
                    ),
                    "business_email": "contact@spinhealth.com",
                },
            },
        },
    ),
    db: Session = Depends(get_db)
):
    return create_enterprise_service(
        db,
        enterprise
    )


@router.get(
    "/",
    response_model=list[EnterpriseListItemResponse],
    status_code=status.HTTP_200_OK,
    summary="Get All Enterprises",
    description="""
Retrieve all active enterprises available in the system.
""",
    responses={
        200: {"description": "Enterprises retrieved successfully"},
        500: {"description": "Internal server error"}
    }
)
def get_enterprises(
    db: Session = Depends(get_db)
):
    return get_all_enterprises_service(db)


@router.get(
    "/{enterprise_id}",
    response_model=EnterpriseDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Enterprise By ID",
    description="""
Retrieve a specific enterprise using its unique identifier.
""",
    responses={
        200: {"description": "Enterprise retrieved successfully"},
        404: {"description": "Enterprise not found"},
        422: {"description": "Validation error"},
        500: {"description": "Internal server error"}
    }
)
def get_enterprise(
    enterprise_id: UUID = Path(
        ...,
        description="Unique identifier of the enterprise",
        example=["550e8400-e29b-41d4-a716-446655440000"]
    ),
    db: Session = Depends(get_db)
):
    return get_enterprise_service(
        db,
        enterprise_id
    )


@router.put(
    "/{enterprise_id}",
    response_model=EnterpriseResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Enterprise",
    description="""
Update an existing enterprise.

Only supplied fields will be updated.
""",
    responses={
        200: {"description": "Enterprise updated successfully"},
        404: {"description": "Enterprise not found"},
        422: {"description": "Validation error"},
        500: {"description": "Internal server error"}
    }
)
def update_enterprise(
    enterprise: EnterpriseUpdate,
    enterprise_id: UUID = Path(
        ...,
        description="Unique identifier of the enterprise"
    ),
    db: Session = Depends(get_db)
):
    return update_enterprise_service(
        db,
        enterprise_id,
        enterprise
    )


@router.delete(
    "/{enterprise_id}",
    status_code=status.HTTP_200_OK,
    summary="Deactivate Enterprise",
    description="""
Marks an enterprise as inactive.

This operation performs a soft delete and does not permanently remove the record.
""",
    responses={
        200: {"description": "Enterprise marked inactive successfully"},
        404: {"description": "Enterprise not found"},
        422: {"description": "Validation error"},
        500: {"description": "Internal server error"}
    }
)
def delete_enterprise(
    enterprise_id: UUID = Path(
        ...,
        description="Unique identifier of the enterprise",
        example=["550e8400-e29b-41d4-a716-446655440000"]
    ),
    db: Session = Depends(get_db)
):
    delete_enterprise_service(
        db,
        enterprise_id
    )

    return {
        "message": "Enterprise marked inactive successfully"
    }