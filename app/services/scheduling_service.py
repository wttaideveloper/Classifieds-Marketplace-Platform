from uuid import UUID
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.models.scheduling_model import Appointment

from app.schemas.scheduling_schema import (
    AppointmentCreate,
    AppointmentUpdate
)

from app.repository.scheduling_repo import (
    create_appointment,
    get_appointment_by_id,
    get_all_appointments,
    update_appointment,
    delete_appointment,
    check_time_conflict
)


def create_appointment_service(
    db: Session,
    payload: AppointmentCreate
):

    if payload.start_time >= payload.end_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="End time must be greater than start time"
        )

    conflict = check_time_conflict(
        db,
        payload.start_time,
        payload.end_time
    )

    if conflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Appointment slot already booked"
        )

    appointment = Appointment(
        **payload.model_dump()
    )

    return create_appointment(
        db,
        appointment
    )


def get_appointment_service(
    db: Session,
    appointment_id: UUID
):

    appointment = get_appointment_by_id(
        db,
        appointment_id
    )

    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found"
        )

    return appointment


def get_all_appointments_service(
    db: Session
):
    return get_all_appointments(db)


def update_appointment_service(
    db: Session,
    appointment_id: UUID,
    payload: AppointmentUpdate
):

    appointment = get_appointment_by_id(
        db,
        appointment_id
    )

    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found"
        )

    update_data = payload.model_dump(
        exclude_unset=True
    )

    start_time = update_data.get(
        "start_time",
        appointment.start_time
    )

    end_time = update_data.get(
        "end_time",
        appointment.end_time
    )

    if start_time >= end_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid appointment time"
        )

    conflict = check_time_conflict(
        db,
        start_time,
        end_time,
        appointment_id
    )

    if conflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Appointment slot already booked"
        )

    for key, value in update_data.items():
        setattr(
            appointment,
            key,
            value
        )

    return update_appointment(
        db,
        appointment
    )


def delete_appointment_service(
    db: Session,
    appointment_id: UUID
):

    appointment = get_appointment_by_id(
        db,
        appointment_id
    )

    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Appointment not found"
        )

    delete_appointment(
        db,
        appointment
    )

    return {
        "message": "Appointment deleted successfully"
    }