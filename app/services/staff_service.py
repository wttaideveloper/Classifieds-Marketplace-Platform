from sqlalchemy.orm import Session
from fastapi import status
from app.models.staff_model import Staff
from app.schemas.staff_schema import StaffCreate, StaffUpdateRequest
from app.exceptions.custom_exception import CustomException
from app.repository.staff_repo import (
    get_staff_repo, 
    get_staff_by_id_repo, 
    get_staff_by_email_repo,
    update_staff_repo,
    delete_staff_repo
)
from uuid import UUID

def create_staff_service(
    payload: StaffCreate,
    db: Session
):
    existing_staff = (
        db.query(Staff)
        .filter(
            Staff.email == payload.email
        )
        .first()
    )
    if existing_staff:
        raise CustomException(
            status_code=status.HTTP_409_CONFLICT,
            message="Staff already exists with this email"
        )
    staff = Staff(
        merchant_id=payload.merchant_id,
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=payload.email,
        phone_number=payload.phone_number,
        role_id=payload.role_id,
        invited_by=payload.invited_by,
        staff_status="PENDING"
    )
    db.add(staff)
    db.commit()
    db.refresh(staff)
    return staff

def get_staff_service(db: Session):

    staff_list = get_staff_repo(db)

    if not staff_list:
        raise CustomException(
            status_code=status.HTTP_404_NOT_FOUND,
            message="No staff found"
        )

    return staff_list

def get_staff_by_id_service(
    db: Session,
    staff_id: UUID
):

    staff = get_staff_by_id_repo(
        db,
        staff_id
    )

    if not staff:
        raise CustomException(
            status_code=status.HTTP_404_NOT_FOUND,
            message="Staff not found"
        )

    return staff


def update_staff_service(
    db: Session,
    staff_id: UUID,
    payload: StaffUpdateRequest
):

    staff = get_staff_by_id_repo(
        db,
        staff_id
    )

    if not staff:
        raise CustomException(
            status_code=status.HTTP_404_NOT_FOUND,
            message="Staff not found"
        )

    if payload.email:

        existing_email = get_staff_by_email_repo(
            db,
            payload.email
        )

        if (
            existing_email and
            existing_email.id != staff.id
        ):
            raise CustomException(
                status_code=status.HTTP_409_CONFLICT,
                message="Email already exists"
            )

    update_data = payload.model_dump(
        exclude_unset=True
    )

    for key, value in update_data.items():
        setattr(staff, key, value)

    return update_staff_repo(
        db,
        staff
    )


def delete_staff_service(
    db: Session,
    staff_id: UUID
):

    staff = get_staff_by_id_repo(
        db,
        staff_id
    )

    if not staff:
        raise CustomException(
            status_code=status.HTTP_404_NOT_FOUND,
            message="Staff not found"
        )

    delete_staff_repo(
        db,
        staff
    )

    return True