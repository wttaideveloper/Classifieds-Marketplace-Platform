from fastapi import APIRouter
from fastapi import Depends
from fastapi import status
from sqlalchemy.orm import Session
from uuid import UUID
from app.db.database import get_db
from app.schemas.staff_schema import (
    StaffCreate,
    StaffCreateResponse,
    StaffListResponse,
    StaffDetailResponse,
    StaffUpdateRequest,
    StaffDeleteResponse,
    UpdateStaffStatusRequest,
    StaffStatusResponse,
    AssignRoleResponse,
    AssignRoleRequest
)
from app.services.staff_service import (
    create_staff_service,
    get_staff_service,
    get_staff_by_id_service,
    update_staff_service,
    delete_staff_service,
    update_staff_status_service,
    assign_role_service
)

router = APIRouter(
    tags=["Staff"]
)

@router.post(
    "/",
    response_model=StaffCreateResponse,
    status_code=status.HTTP_201_CREATED
)
def create_staff(
    payload: StaffCreate,
    db: Session = Depends(get_db)
):
    staff = create_staff_service(
        payload,
        db
    )
    return {
        "success": True,
        "message": "Staff created successfully",
        "data": staff
    }

@router.get(
    "/",
    response_model=StaffListResponse,
    status_code=status.HTTP_200_OK
)
def get_staff(
    db: Session = Depends(get_db)
):

    staff = get_staff_service(db)

    return {
        "success": True,
        "message": "Staff fetched successfully",
        "data": staff
    }

@router.get(
    "/{staff_id}",
    response_model=StaffDetailResponse,
    status_code=status.HTTP_200_OK
)
def get_staff(
    staff_id: UUID,
    db: Session = Depends(get_db)
):

    staff = get_staff_by_id_service(
        db,
        staff_id
    )

    return {
        "success": True,
        "message": "Staff fetched successfully",
        "data": staff
    }


@router.put(
    "/{staff_id}",
    response_model=StaffDetailResponse,
    status_code=status.HTTP_200_OK
)
def update_staff(
    payload: StaffUpdateRequest,
    staff_id: UUID,
    db: Session = Depends(get_db)
):

    staff = update_staff_service(
        db,
        staff_id,
        payload
    )

    return {
        "success": True,
        "message": "Staff updated successfully",
        "data": staff
    }


@router.delete(
    "/{staff_id}",
    response_model=StaffDeleteResponse,
    status_code=status.HTTP_200_OK
)
def delete_staff(
    staff_id: UUID,
    db: Session = Depends(get_db)
):

    delete_staff_service(
        db,
        staff_id
    )

    return {
        "success": True,
        "message": "Staff deleted successfully"
    }

@router.put(
    "/{staff_id}/status",
    response_model=StaffStatusResponse,
    status_code=status.HTTP_200_OK
)
def update_staff_status(
    staff_id: UUID,
    payload: UpdateStaffStatusRequest,
    db: Session = Depends(get_db)
):
    result = update_staff_status_service(
        db=db,
        staff_id=staff_id,
        payload=payload
    )

    return {
        "success": True,
        "message": "Staff status updated successfully",
        "data": result
    }

@router.post(
    "/{staff_id}/roles",
    response_model=AssignRoleResponse,
    status_code=status.HTTP_200_OK
)
def assign_role(
    staff_id: UUID,
    payload: AssignRoleRequest,
    db: Session = Depends(get_db)
):

    result = assign_role_service(
        db=db,
        staff_id=staff_id,
        payload=payload
    )

    return {
        "success": True,
        "message": "Role assigned successfully",
        "data": result
    }