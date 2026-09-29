from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import require_form_configuration_super_admin
from app.db.database import get_db
from app.schemas.event_type_schema import EventTypeCreate, EventTypeResponse, EventTypeUpdate
from app.services.event_type_service import (
    create_event_type_service,
    delete_event_type_service,
    get_event_type_service,
    list_event_types_service,
    update_event_type_service,
)

router = APIRouter(tags=["Event Types"])


@router.get(
    "/",
    response_model=list[EventTypeResponse],
    summary="List Event Types",
    description=(
        "Public reference data for the Enterprise Create/Edit Event flow: every module rule an Event Type "
        "carries (default/allowed/required) comes from here, never hardcoded on the client. Returns ACTIVE "
        "Event Types only by default; pass include_inactive=true (admin tooling) to see deactivated ones too — "
        "deactivating a type never affects Events that already use it."
    ),
)
def list_event_types(
    include_inactive: bool = Query(False, description="Also return deactivated Event Types (admin tooling)."),
    db: Session = Depends(get_db),
):
    return list_event_types_service(db, include_inactive=include_inactive)


@router.post(
    "/",
    response_model=EventTypeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Event Type",
    description="Super Admin only. `key` is permanent once created. `allowed_modules` defaults to "
                "\"everything allowed\" when omitted; `required_modules` defaults to \"registration only\" "
                "when omitted. `registration` is mandatory for every Event Type and cannot be disallowed, "
                "defaulted off, or left non-required.",
)
def create_event_type(
    payload: EventTypeCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_form_configuration_super_admin),
):
    return create_event_type_service(db, payload)


@router.get(
    "/{event_type_id}",
    response_model=EventTypeResponse,
    summary="Get Event Type",
)
def get_event_type(event_type_id: UUID = Path(...), db: Session = Depends(get_db)):
    return get_event_type_service(db, event_type_id)


@router.patch(
    "/{event_type_id}",
    response_model=EventTypeResponse,
    summary="Update Event Type",
    description=(
        "Super Admin only. `key` cannot be changed once created. Editing default/allowed/required modules "
        "never touches any Event already created under this type — those keep their own persisted `modules` "
        "forever (see the Phase 2.2 contract)."
    ),
)
def update_event_type(
    payload: EventTypeUpdate,
    event_type_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_form_configuration_super_admin),
):
    return update_event_type_service(db, event_type_id, payload)


@router.delete(
    "/{event_type_id}",
    summary="Delete Event Type",
    description="Super Admin only. 409 if any Event references this type's key — deactivate it (active=false) instead.",
)
def delete_event_type(
    event_type_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_form_configuration_super_admin),
):
    return delete_event_type_service(db, event_type_id)
