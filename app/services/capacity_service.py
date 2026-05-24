from sqlalchemy.orm import Session
from app.models.capacity_model import (
    ListingCapacity,
    CapacityHistory,
    CapacityStatus
)
from app.repository.capacity_repo import (
    create_capacity,
    get_capacity_by_listing_id,
    update_capacity,
    create_capacity_history,
    get_capacity_history_by_listing_id
)
from app.exceptions.custom_exception import CustomException
from uuid import uuid4

def create_capacity_service(
    db: Session,
    payload
):
    existing_capacity = get_capacity_by_listing_id(
        db,
        payload.listing_id
    )
    if existing_capacity:
        raise CustomException(
            409,
            "Capacity already exists for this listing"
        )
    if payload.total_capacity <= 0:
        raise CustomException(
            400,
            "Total capacity must be greater than 0"
        )
    capacity = ListingCapacity(
        id=uuid4(),
        listing_id=payload.listing_id,
        total_capacity=payload.total_capacity,
        booked_capacity=0,
        available_capacity=payload.total_capacity,
        waitlist_enabled=payload.waitlist_enabled,
        capacity_status=CapacityStatus.OPEN
    )
    created = create_capacity(
        db,
        capacity
    )
    return {
        "success": True,
        "message": "Capacity created successfully",
        "data": created
    }

def get_capacity_service(
    db: Session,
    listing_id
):
    capacity = get_capacity_by_listing_id(
        db,
        listing_id
    )
    if not capacity:
        raise CustomException(
            404,
            "Capacity not found"
        )
    history = get_capacity_history_by_listing_id(
        db,
        listing_id
    )
    return {
        "success": True,
        "message": "Capacity fetched successfully",
        "data": {
            "capacity": capacity,
            "history": history
        }
    }

def update_capacity_service(
    db: Session,
    listing_id,
    payload
):
    capacity = get_capacity_by_listing_id(
        db,
        listing_id
    )
    if not capacity:
        raise CustomException(
            404,
            "Capacity not found"
        )
    if payload.total_capacity < capacity.booked_capacity:
        raise CustomException(
            400,
            "Total capacity cannot be less than booked capacity"
        )
    old_capacity = capacity.total_capacity
    capacity.total_capacity = payload.total_capacity
    capacity.available_capacity = (
        payload.total_capacity -
        capacity.booked_capacity
    )
    capacity.waitlist_enabled = payload.waitlist_enabled
    if capacity.available_capacity == 0:
        capacity.capacity_status = CapacityStatus.FULL
    elif capacity.available_capacity > 0:
        capacity.capacity_status = CapacityStatus.OPEN
    updated_capacity = update_capacity(
        db,
        capacity
    )
    history = CapacityHistory(
        id=uuid4(),
        listing_id=listing_id,
        old_capacity=old_capacity,
        new_capacity=payload.total_capacity,
        remarks=payload.remarks
    )
    create_capacity_history(
        db,
        history
    )
    return {
        "success": True,
        "message": "Capacity updated successfully",
        "data": {
            "listing_id": updated_capacity.listing_id,
            "total_capacity": updated_capacity.total_capacity,
            "booked_capacity": updated_capacity.booked_capacity,
            "available_capacity": updated_capacity.available_capacity,
            "capacity_status": updated_capacity.capacity_status
        }
    }

def get_capacity_availability_service(
    db,
    listing_id
):
    capacity = get_capacity_by_listing_id(
        db,
        listing_id
    )
    if not capacity:
        raise CustomException(
            404,
            "Capacity not found"
        )
    booking_allowed = False
    if (
        capacity.capacity_status != CapacityStatus.CLOSED
        and
        capacity.available_capacity > 0
    ):
        booking_allowed = True
    return {
        "success": True,
        "message": "Capacity availability fetched successfully",
        "data": {
            "total_capacity": capacity.total_capacity,
            "available_capacity": capacity.available_capacity,
            "booking_allowed": booking_allowed,
            "capacity_status": capacity.capacity_status
        }
    }

def update_capacity_status_service(
    db,
    listing_id,
    payload
):
    capacity = get_capacity_by_listing_id(
        db,
        listing_id
    )
    if not capacity:
        raise CustomException(
            404,
            "Capacity not found"
        )
    if capacity.total_capacity <= 0:
        raise CustomException(
            400,
            "Total capacity must be greater than 0"
        )
    if capacity.booked_capacity > capacity.total_capacity:
        raise CustomException(
            400,
            "Booked capacity cannot exceed total capacity"
        )
    available_capacity = (
        capacity.total_capacity -
        capacity.booked_capacity
    )
    if available_capacity < 0:
        raise CustomException(
            400,
            "Available capacity cannot be negative"
        )
    if (
        payload.capacity_status == CapacityStatus.OPEN
        and
        available_capacity == 0
    ):
        raise CustomException(
            400,
            "Cannot set status OPEN when capacity is full"
        )
    if (
        payload.capacity_status == CapacityStatus.FULL
        and
        available_capacity > 0
    ):
        raise CustomException(
            400,
            "Cannot set status FULL when slots are available"
        )
    capacity.available_capacity = available_capacity
    capacity.capacity_status = payload.capacity_status
    updated_capacity = update_capacity(
        db,
        capacity
    )
    return {
        "success": True,
        "message": "Capacity status updated successfully",
        "data": {
            "listing_id": updated_capacity.listing_id,
            "total_capacity": updated_capacity.total_capacity,
            "booked_capacity": updated_capacity.booked_capacity,
            "available_capacity": updated_capacity.available_capacity,
            "capacity_status": updated_capacity.capacity_status
        }
    }