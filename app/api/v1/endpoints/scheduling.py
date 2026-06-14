from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    status
)

from sqlalchemy.orm import Session

from app.db.database import get_db

from app.schemas.scheduling_schema import (
    AppointmentCreate,
    AppointmentUpdate,
    AppointmentResponse
)

from app.services.scheduling_service import (
    create_appointment_service,
    get_appointment_service,
    get_all_appointments_service,
    update_appointment_service,
    delete_appointment_service
)

router = APIRouter(
    tags=["Scheduling"]
)


@router.post(
    "/appointments",
    response_model=AppointmentResponse,
    status_code=status.HTTP_201_CREATED
)
def create_appointment(
    payload: AppointmentCreate,
    db: Session = Depends(get_db)
):
    return create_appointment_service(
        db,
        payload
    )


@router.get(
    "/appointments",
    response_model=list[AppointmentResponse],
    status_code=status.HTTP_200_OK
)
def get_all_appointments(
    db: Session = Depends(get_db)
):
    return get_all_appointments_service(db)


@router.get(
    "/appointments/{appointment_id}",
    response_model=AppointmentResponse,
    status_code=status.HTTP_200_OK
)
def get_appointment(
    appointment_id: UUID,
    db: Session = Depends(get_db)
):
    return get_appointment_service(
        db,
        appointment_id
    )


@router.put(
    "/appointments/{appointment_id}",
    response_model=AppointmentResponse,
    status_code=status.HTTP_200_OK
)
def update_appointment(
    appointment_id: UUID,
    payload: AppointmentUpdate,
    db: Session = Depends(get_db)
):
    return update_appointment_service(
        db,
        appointment_id,
        payload
    )


@router.delete(
    "/appointments/{appointment_id}",
    status_code=status.HTTP_200_OK
)
def delete_appointment(
    appointment_id: UUID,
    db: Session = Depends(get_db)
):
    return delete_appointment_service(
        db,
        appointment_id
    )