from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db.database import get_db

from app.schemas.communication_schema import (
    CommunicationCreate,
    CommunicationUpdate,
    CommunicationResponse
)

from app.services.communication_service import (
    create_communication_service,
    get_all_communications_service,
    get_communication_service,
    update_communication_service,
    delete_communication_service
)

router = APIRouter(
    tags=["Communications"]
)

@router.post(
    "/",
    response_model=CommunicationResponse,
    status_code=status.HTTP_201_CREATED
)
def create_communication(
        payload: CommunicationCreate,
        db: Session = Depends(get_db)
):
    return create_communication_service(
        db,
        payload
    )

@router.get(
    "/",
    response_model=list[CommunicationResponse]
)
def get_all_communications(
        db: Session = Depends(get_db)
):
    return get_all_communications_service(
        db
    )

@router.get(
    "/{communication_id}",
    response_model=CommunicationResponse
)
def get_communication(
        communication_id: UUID,
        db: Session = Depends(get_db)
):
    return get_communication_service(
        db,
        communication_id
    )

@router.put(
    "/{communication_id}",
    response_model=CommunicationResponse
)
def update_communication(
        communication_id: UUID,
        payload: CommunicationUpdate,
        db: Session = Depends(get_db)
):
    return update_communication_service(
        db,
        communication_id,
        payload
    )

@router.delete(
    "/{communication_id}",
    status_code=status.HTTP_200_OK
)
def delete_communication(
        communication_id: UUID,
        db: Session = Depends(get_db)
):
    return delete_communication_service(
        db,
        communication_id
    )