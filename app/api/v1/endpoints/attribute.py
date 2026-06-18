from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Query,
    Path,
    status
)

from sqlalchemy.orm import Session

from app.db.database import get_db

from app.schemas.attribute_schema import (
    AttributeCreate,
    AttributeUpdate,
    AttributeResponse
)

from app.services.attribute_service import (
    create_attribute_service,
    get_attributes_service,
    update_attribute_service,
    delete_attribute_service
)

router = APIRouter(
    tags=["Dynamic Attributes"]
)


@router.post(
    "",
    response_model=AttributeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Dynamic Attribute",
    description="""
Create a new dynamic attribute for an entity.

Dynamic attributes allow additional custom fields to be associated with:
- Enterprise
- Product
- Service

Example attributes:
- Color
- Size
- Weight
- Brand
- Category
""",
    responses={
        201: {"description": "Attribute created successfully"},
        400: {"description": "Invalid request data"},
        422: {"description": "Validation error"},
        500: {"description": "Internal server error"}
    }
)
def create_attribute(
    attribute: AttributeCreate,
    db: Session = Depends(get_db)
):
    return create_attribute_service(
        db,
        attribute
    )


@router.get(
    "",
    response_model=list[AttributeResponse],
    status_code=status.HTTP_200_OK,
    summary="Get Dynamic Attributes",
    description="""
Retrieve all attributes associated with a specific entity.

Supported entity types:
- enterprise
- product
- service

Returns a list of dynamic attributes attached to the provided entity.
""",
    responses={
        200: {"description": "Attributes retrieved successfully"},
        404: {"description": "Entity not found"},
        422: {"description": "Validation error"},
        500: {"description": "Internal server error"}
    }
)
def get_attributes(
    entity_type: str = Query(
        ...,
        description="Entity type (enterprise, product, service)",
        example="product"
    ),
    entity_id: UUID = Query(
        ...,
        description="Unique identifier of the entity",
        example="550e8400-e29b-41d4-a716-446655440000"
    ),
    db: Session = Depends(get_db)
):
    return get_attributes_service(
        db,
        entity_type,
        entity_id
    )


@router.put(
    "/{attribute_id}",
    response_model=AttributeResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Dynamic Attribute",
    description="""
Update an existing dynamic attribute.

Only the provided fields will be updated.
""",
    responses={
        200: {"description": "Attribute updated successfully"},
        404: {"description": "Attribute not found"},
        422: {"description": "Validation error"},
        500: {"description": "Internal server error"}
    }
)
def update_attribute(
    attribute_id: UUID = Path(
        ...,
        description="Unique identifier of the attribute",
        example="550e8400-e29b-41d4-a716-446655440000"
    ),
    attribute: AttributeUpdate = ...,
    db: Session = Depends(get_db)
):
    return update_attribute_service(
        db,
        attribute_id,
        attribute
    )


@router.delete(
    "/{attribute_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete Dynamic Attribute",
    description="""
Delete a dynamic attribute using its unique identifier.
""",
    responses={
        200: {
            "description": "Attribute deleted successfully"
        },
        404: {
            "description": "Attribute not found"
        },
        500: {
            "description": "Internal server error"
        }
    }
)
def delete_attribute(
    attribute_id: UUID = Path(
        ...,
        description="Unique identifier of the attribute",
        example="550e8400-e29b-41d4-a716-446655440000"
    ),
    db: Session = Depends(get_db)
):
    delete_attribute_service(
        db,
        attribute_id
    )

    return {
        "message": "Attribute deleted successfully"
    }