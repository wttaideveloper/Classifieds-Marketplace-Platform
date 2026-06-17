from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Query,
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
    status_code=status.HTTP_201_CREATED
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
    status_code=status.HTTP_200_OK
)
def get_attributes(
    entity_type: str = Query(...),
    entity_id: UUID = Query(...),
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
    status_code=status.HTTP_200_OK
)
def update_attribute(
    attribute_id: UUID,
    attribute: AttributeUpdate,
    db: Session = Depends(get_db)
):
    return update_attribute_service(
        db,
        attribute_id,
        attribute
    )


@router.delete(
    "/{attribute_id}",
    status_code=status.HTTP_200_OK
)
def delete_attribute(
    attribute_id: UUID,
    db: Session = Depends(get_db)
):
    delete_attribute_service(
        db,
        attribute_id
    )

    return {
        "message":
        "Attribute deleted successfully"
    }