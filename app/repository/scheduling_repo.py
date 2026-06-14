from uuid import UUID
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.models.scheduling_model import Appointment


def create_appointment(db: Session, appointment: Appointment):

    db.add(appointment)
    db.commit()
    db.refresh(appointment)

    return appointment


def get_appointment_by_id(
    db: Session,
    appointment_id: UUID
):
    return (
        db.query(Appointment)
        .filter(Appointment.id == appointment_id)
        .first()
    )


def get_all_appointments(
    db: Session
):
    return db.query(Appointment).all()


def delete_appointment(
    db: Session,
    appointment: Appointment
):
    db.delete(appointment)
    db.commit()


def update_appointment(
    db: Session,
    appointment: Appointment
):
    db.commit()
    db.refresh(appointment)

    return appointment


def check_time_conflict(
    db: Session,
    start_time,
    end_time,
    exclude_id=None
):
    query = db.query(Appointment).filter(
        and_(
            Appointment.start_time < end_time,
            Appointment.end_time > start_time
        )
    )

    if exclude_id:
        query = query.filter(
            Appointment.id != exclude_id
        )

    return query.first()