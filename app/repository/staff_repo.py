from sqlalchemy.orm import Session
from app.models.staff_model import (
    Staff,
    Role,
    StaffInvitation
)
from uuid import UUID

def get_staff_repo(db: Session):

    return (
        db.query(
            Staff.id,
            Staff.merchant_id,
            Staff.first_name,
            Staff.last_name,
            Staff.email,
            Staff.phone_number,
            Staff.role_id,
            Staff.staff_status,
            Staff.invited_by,
            Staff.joined_at,
            Staff.created_at,
            Staff.updated_at,
            StaffInvitation.invitation_status,
            StaffInvitation.expires_at
        )
        .outerjoin(
            StaffInvitation,
            Staff.email == StaffInvitation.email
        )
        .all()
    )

def get_staff_by_id_repo(
    db: Session,
    staff_id: UUID
):
    return (
        db.query(Staff)
        .filter(Staff.id == staff_id)
        .first()
    )

def get_staff_by_email_repo(
    db: Session,
    email: str
):
    return (
        db.query(Staff)
        .filter(Staff.email == email)
        .first()
    )

def update_staff_repo(
    db: Session,
    staff
):
    db.commit()
    db.refresh(staff)
    return staff


def delete_staff_repo(
    db: Session,
    staff
):
    db.delete(staff)
    db.commit()

def update_staff_status_repo(
    db: Session,
    staff: Staff
):
    db.commit()
    db.refresh(staff)
    return staff

def get_role_by_id_repo(
    db: Session,
    role_id: UUID
):
    return (
        db.query(Role)
        .filter(Role.id == role_id)
        .first()
    )

def assign_role_repo(
    db: Session,
    staff: Staff
):
    db.commit()
    db.refresh(staff)
    return staff