from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from sqlalchemy.orm import Session

from app.core.dependencies import (
    extract_access_token,
    get_current_admin,
    get_current_super_admin,
    get_current_user,
    get_optional_current_user,
    get_web_session_cookie_token,
    require_event_form_builder_admin,
    require_roles,
)
from app.core.config import settings
from app.db.database import get_db
from app.repository.event_repo import (
    STAFF_ROLES,
    assert_event_access,
    build_event_viewer,
    can_view_event,
    is_platform_super_admin,
    require_event_owner,
    resolve_caller_tenant_id,
)
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.event_management_schema import (
    AttendeePaymentStatus,
    AttendeeSort,
    EventAttendeePaginatedResponse,
    EventAttendeeResponse,
    EventDashboardResponse,
    RegistrationSource,
    RegistrationStatusFilter,
)
from app.schemas.event_meal_schema import EventMealSelectionResponse, EventMealSelectionUpdate
from app.schemas.event_walk_in_schema import EventWalkInRequest, EventWalkInResponse
from app.schemas.event_session_attendance_schema import (
    EventSessionAttendanceResponse,
    EventSessionBatchCheckInRequest,
    EventSessionBatchCheckInResponse,
    EventSessionCheckInRequest,
    EventSessionCheckOutRequest,
    EventSessionUncheckInRequest,
)
from app.schemas.event_schema import (
    EventCreate,
    EventDetailResponse,
    EventPaginatedResponse,
    EventResponse,
    EventStatusUpdate,
    EventUpdate,
)
from app.schemas.event_schema import (
    EventAnnouncementCreate,
    EventBatchCheckInRequest,
    EventCheckInRequest,
    EventCheckOutRequest,
    EventCheckoutRequest,
    EventOrderResponse,
    EventRefundRequest,
    EventRegistrationCreate,
    EventSessionCreate,
    EventSessionUpdate,
    EventTemplateApplyRequest,
    EventTemplateCreateRequest,
    EventBatchCheckInPreviewItem,
    EventBatchCheckInResponse,
    EventTemplateDeleteResponse,
    EventTemplateResponse,
    EventTemplateUpdateRequest,
    EventUncheckInRequest,
    MyWaitlistResponse,
)
from app.services.event_attendee_service import (
    AttendeeFilters,
    export_attendees_csv,
    get_attendee_service,
    list_attendees_service,
)
from app.services.event_dashboard_service import get_event_dashboard_service
from app.services.event_meal_service import update_registration_meals_service
from app.services.event_walk_in_service import create_walk_in_service
from app.services.event_session_attendance_service import (
    batch_check_in_session_service,
    check_in_session_service,
    check_out_session_service,
    uncheck_in_session_service,
)
from app.services.event_service import (
    get_template_service,
    add_session_service,
    apply_template_service,
    check_in_service,
    check_out_service,
    contact_organiser_service,
    create_event_checkout_service,
    create_event_refund_service,
    create_event_service,
    create_feedback_service,
    create_registration_service,
    create_template_service,
    create_waitlist_entry_service,
    delete_event_service,
    delete_session_service,
    delete_waitlist_entry_service,
    duplicate_event_service,
    get_event_attendance_service,
    get_event_feedbacks_service,
    get_event_orders_service,
    get_event_registrations_service,
    get_event_reports_service,
    get_event_service,
    get_event_summary_service,
    get_event_waitlist_service,
    get_events_service,
    get_meeting_link_service,
    get_sessions_service,
    list_templates_service,
    moderate_review_service,
    my_registrations_service,
    send_announcement_service,
    uncheck_in_service,
    update_event_service,
    update_event_status_service,
    update_session_service,
    validate_qr_service,
)

router = APIRouter(tags=["Events"])


def _event_manager(*allowed_roles: str):
    """Build the single reusable authorization dependency for event-scoped staff routes.

    Order matters and is deliberate: authenticate (401) -> role gate (403) -> load the
    Event (404) -> verify tenant ownership (403). All of it runs before the handler body,
    so nothing is read or mutated for a caller who does not own the Event. ``event_id``
    binds to the route's path parameter of the same name.
    """

    def dependency(
        request: Request,
        event_id: UUID = Path(..., description="Event ID"),
        db: Session = Depends(get_db),
        current_user: dict = Depends(get_current_user),
    ) -> dict:
        if current_user.get("role") not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
        require_event_owner(db, event_id, current_user, access_token=extract_access_token(request))
        return current_user

    return dependency


# Same role gate the routes had before (require_roles(["admin","provider"])), plus ownership.
require_event_manager = _event_manager("admin", "provider")
# Tenant-owner-only routes (formerly require_roles(["admin"])), plus ownership.
require_event_admin = _event_manager("admin")
# Attendee management + dashboard: the owning admin/provider, or an ACTIVE Platform Super Admin. The role gate
# lets "super_admin" reach ownership; assert_event_access still rejects an inactive one.
require_event_staff = _event_manager("admin", "provider", "super_admin")


def attendee_filters(
    q: str | None = Query(None, max_length=200, description="Case-insensitive search over participant name, email and registration reference (or an exact registration id)."),
    status_filter: RegistrationStatusFilter | None = Query(None, alias="status", description="Registration status."),
    ticket_type_id: str | None = Query(None, max_length=100, description="Ticket type id."),
    payment_status: AttendeePaymentStatus | None = Query(None, description="Derived from the paired order; 'free' / 'unpaid' mean no order (free / paid event)."),
    checked_in: bool | None = Query(None, description="true = checked in (status 'attended'); false = not checked in."),
    source: RegistrationSource | None = Query(None, description="walk_in = registered by an organizer at the venue; online = everything else."),
    registered_from: date | None = Query(None, description="Registered on or after this date (YYYY-MM-DD, UTC)."),
    registered_to: date | None = Query(None, description="Registered on or before this date (YYYY-MM-DD, UTC)."),
    sort: AttendeeSort = Query("newest", description="newest | oldest | name | email (ties broken by id)."),
) -> AttendeeFilters:
    return AttendeeFilters(
        q=q, status=status_filter, ticket_type_id=ticket_type_id, payment_status=payment_status,
        checked_in=checked_in, source=source, registered_from=registered_from, registered_to=registered_to, sort=sort,
    )


@router.post("/", response_model=EventResponse, status_code=status.HTTP_201_CREATED, summary="Create Event")
def create_event(event: EventCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return create_event_service(db, event, current_user)


@router.get(
    "/",
    response_model=EventPaginatedResponse,
    status_code=status.HTTP_200_OK,
    summary="List Events",
    description=(
        "Public browsing returns **published** events only. Staff (admin/provider) additionally see every "
        "event of their own tenant in any status; an active Platform Super Admin sees all. Authentication is "
        "optional — an absent or invalid token is treated as an anonymous public reader."
    ),
)
def list_events(
    request: Request,
    search: str | None = Query(None, description="Search across title/description/category."),
    category: str | None = Query(None, description="Filter by category."),
    tenant_id: UUID | None = Query(
        None,
        description="Filter events by tenant ID. Omit for global list (all tenants).",
    ),
    enterprise_id: UUID | None = Query(None, description="Filter by enterprise ID."),
    location_id: UUID | None = Query(None, description="Filter by location ID."),
    status_filter: str | None = Query(None, alias="status", description="Filter by status."),
    delivery_mode: str | None = Query(None, description="Filter by delivery mode."),
    date_from: str | None = Query(None, description="Filter start_date >= YYYY-MM-DD"),
    date_to: str | None = Query(None, description="Filter end_date <= YYYY-MM-DD"),
    min_price: str | None = Query(None, description="Min price"),
    max_price: str | None = Query(None, description="Max price"),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    current_user: dict | None = Depends(get_optional_current_user),
):
    token = extract_access_token(request)
    viewer = build_event_viewer(
        db,
        current_user,
        access_token=token,
        # Only ask the identity service about Platform Super Admin when the request
        # targets non-public statuses; customer/mobile traffic (status=published) never does.
        remote_platform_check=bool(status_filter) and status_filter != "published",
    )
    return get_events_service(
        db,
        viewer=viewer,
        search=search,
        category=category,
        tenant_id=tenant_id,
        enterprise_id=enterprise_id,
        location_id=location_id,
        status_filter=status_filter,
        delivery_mode=delivery_mode,
        date_from=date_from,
        date_to=date_to,
        min_price=min_price,
        max_price=max_price,
        page=page,
        page_size=page_size,
    )


@router.get("/my/registrations", summary="My registrations — upcoming/completed/cancelled")
def my_registrations(status: str | None = Query(None, description="Filter by registration status: confirmed|attended|cancelled"), db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    email = current_user.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Email not found in token")
    return my_registrations_service(db, email, status)


@router.get(
    "/my/waitlist",
    response_model=list[MyWaitlistResponse],
    summary="My waitlist entries — waiting/promoted/left"
)
def my_waitlist(
    status: str | None = Query(None, description="Filter by status: waiting|promoted|left"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    from app.services.event_service import my_waitlist_service
    email = current_user.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Email not found in token")
    return my_waitlist_service(db, email, status)


@router.post(
    "/templates",
    response_model=EventTemplateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Template",
    description="Create a template under tenant ownership. Frontend may send tenant_id from /tenant/me when auth token lacks tenant claim. enterprise_id optional.",
    responses={
        403: {"description": "Supplied tenant_id does not belong to authenticated user", "content": {"application/json": {"example": {"detail": "Supplied tenant_id does not belong to authenticated user"}}}},
    },
)
def create_template(request: Request, payload: EventTemplateCreateRequest, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return create_template_service(db, payload.model_dump(exclude_unset=True), current_user, access_token=extract_access_token(request))


@router.get("/templates", response_model=list[EventTemplateResponse], summary="List Templates", description="List templates owned by authenticated tenant (tenant isolation).")
def list_templates(request: Request, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return list_templates_service(db, current_user, access_token=extract_access_token(request))


@router.get(
    "/templates/{template_id}",
    response_model=EventTemplateResponse,
    summary="Get Template by ID",
    responses={
        404: {"description": "Template not found", "content": {"application/json": {"example": {"detail": "Template not found"}}}},
        403: {"description": "Tenant mismatch", "content": {"application/json": {"example": {"detail": "Template does not belong to your tenant"}}}},
    },
)
def get_template(request: Request, template_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return get_template_service(db, template_id, current_user, access_token=extract_access_token(request))


@router.post(
    "/templates/{template_id}/apply",
    response_model=EventResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Apply Template",
    description="Create an incomplete draft using the tenant's current active Event Form. Map compatible core/custom/composite values; ignore removed fields and never select the template's historical version. Complete required fields before submission. Accepts optional tenant_id/enterprise_id.",
    responses={
        404: {"description": "Template not found", "content": {"application/json": {"example": {"detail": "Template not found"}}}},
        403: {"description": "Tenant mismatch", "content": {"application/json": {"example": {"detail": "Template does not belong to your tenant"}}}},
        400: {"description": "Invalid template data", "content": {"application/json": {"example": {"detail": "Failed to create Event from template: ..."}}}},
    },
)
def apply_template(request: Request, template_id: UUID, payload: EventTemplateApplyRequest, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    return apply_template_service(db, template_id, payload.model_dump(exclude_unset=True), current_user, access_token=extract_access_token(request))


@router.get(
    "/form-configuration/active",
    summary="Resolved active Event form for authenticated Enterprise Admin",
    description=(
        "Server-side resolution from authenticated session/JWT (query `tenant_id` is ignored): "
        "authenticate → resolve tenant from token → active selective config "
        "→ else active global → else legacy default. Returns full sections/fields for dynamic rendering."
    ),
)
def get_active_event_form_configuration(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_event_form_builder_admin),
):
    from app.schemas.event_form_config_schema import ActiveFormConfigurationResponse
    from app.services.event_form_config_service import get_active_form_configuration_service

    access_token = None
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        access_token = auth_header.split(" ", 1)[1].strip()
    if not access_token:
        access_token = get_web_session_cookie_token(request)

    return ActiveFormConfigurationResponse.model_validate(
        get_active_form_configuration_service(db, current_user, access_token=access_token)
    )


@router.get(
    "/{event_id}/registration-form",
    summary="Get Registration Form (Customer)",
    description=(
        "Returns the dynamic registration form fields for a **published** event. "
        "Available to any authenticated user (customer, admin, provider). "
        "Raises 404 for non-existent events and 403 for non-published events "
        "so customers cannot retrieve configuration for events they cannot register for."
    ),
    responses={
        403: {"description": "Event is not published"},
        404: {"description": "Event not found"},
    },
)
def get_event_registration_form(
    event_id: UUID = Path(..., description="Event ID"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Customer-safe form configuration endpoint.

    - Requires authentication (any role).
    - Only returns form config for *published* events.
    - Does NOT expose internal admin diagnostics, tenant internals, or
      private configuration not needed by the mobile registration flow.
    """
    from app.repository.event_repo import get_event_by_id
    from app.services.event_form_config_service import get_event_form_configuration_service

    # Verify the event exists and is in a state the customer can register for.
    event = get_event_by_id(db, event_id)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    if event.status != "published":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Registration form is only available for published events "
                f"(current status: {event.status})"
            ),
        )

    # Reuse the existing service — it returns sections/fields sufficient for
    # the mobile registration flow without exposing admin-only internals.
    return get_event_form_configuration_service(db, event_id, current_user)


@router.get(
    "/{event_id}/form-configuration",
    summary="Historical Event form version used by this Event",
    description="Loads the exact published configuration version stored on the Event at creation time.",
)
def get_event_form_configuration(
    event_id: UUID = Path(..., description="Event ID"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_event_manager),
):
    from app.schemas.event_form_config_schema import ActiveFormConfigurationResponse
    from app.services.event_form_config_service import get_event_form_configuration_service
    return ActiveFormConfigurationResponse.model_validate(get_event_form_configuration_service(db, event_id, current_user))


@router.get(
    "/{event_id}",
    response_model=EventDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Event by ID",
    description=(
        "Published events are public. A non-published event is returned only to staff of its owning tenant, an "
        "active Platform Super Admin, or a participant who registered for / joined the waitlist of it (so My Events "
        "still opens cancelled or completed events). Everyone else gets 404. Internal workflow fields "
        "(last_admin_notes, requires_reapproval) and organiser_contact are returned to owners only."
    ),
)
def get_event(
    request: Request,
    event_id: UUID = Path(..., description="Event ID"),
    db: Session = Depends(get_db),
    current_user: dict | None = Depends(get_optional_current_user),
):
    token = extract_access_token(request)
    viewer = build_event_viewer(db, current_user, access_token=token)
    return get_event_service(db, event_id, viewer=viewer, access_token=token)


@router.put("/{event_id}", response_model=EventResponse, status_code=status.HTTP_200_OK, summary="Update Event")
def update_event(event: EventUpdate, event_id: UUID = Path(..., description="Event ID"), db: Session = Depends(get_db), current_user: dict = Depends(require_event_manager)):
    return update_event_service(db, event_id, event, current_user)


@router.delete("/{event_id}", status_code=status.HTTP_200_OK, summary="Delete Event")
def delete_event(event_id: UUID = Path(..., description="Event ID"), db: Session = Depends(get_db), current_user: dict = Depends(require_event_manager)):
    delete_event_service(db, event_id, current_user)
    return {"message": "Event deleted successfully"}


@router.post("/{event_id}/duplicate", response_model=EventResponse, status_code=status.HTTP_201_CREATED, summary="Duplicate Event")
def duplicate_event(event_id: UUID = Path(..., description="Event ID"), db: Session = Depends(get_db), current_user: dict = Depends(require_event_manager)):
    return duplicate_event_service(db, event_id, current_user)


@router.patch("/{event_id}/status", response_model=EventResponse, status_code=status.HTTP_200_OK, summary="Update Event Status")
def update_status(
    event_id: UUID,
    payload: EventStatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Enterprise lifecycle transitions for admin/provider; Platform approvals for Super Admin."""
    from app.services.super_admin_identity import resolve_platform_super_admin_user

    token = None
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
    if not token:
        token = get_web_session_cookie_token(request)

    resolved_super = resolve_platform_super_admin_user(current_user, access_token=token)
    if resolved_super:
        current_user = resolved_super

    role = current_user.get("role")
    _super_admin_only = {"approved", "rejected", "needs_revision"}
    if payload.status in _super_admin_only:
        if role not in ("admin", "super_admin"):
            raise HTTPException(status_code=403, detail="Super Admin access required")
    elif role not in ("admin", "provider", "super_admin"):
        if not settings.is_production and current_user.get("id") == settings.DEV_DEFAULT_USER_ID:
            pass
        else:
            raise HTTPException(status_code=403, detail="Not authorized")

    # Tenant ownership (an active Platform Super Admin passes across tenants). Runs before any
    # transition, so a cross-tenant caller can neither change nor probe another tenant's event.
    require_event_owner(db, event_id, current_user, access_token=token, include_deleted=True)
    return update_event_status_service(db, event_id, payload.status, current_user)


@router.post("/{event_id}/unpublish", response_model=EventResponse, status_code=status.HTTP_200_OK, summary="Unpublish Event")
def unpublish_event(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_event_manager)):
    if current_user.get("role") not in ("admin", "super_admin"):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Only Enterprise Admin can unpublish events (acting as Super Admin for testing).")
    return update_event_status_service(db, event_id, "approved", current_user)


@router.post("/{event_id}/archive", response_model=EventResponse, status_code=status.HTTP_200_OK, summary="Archive Event")
def archive_event(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_event_manager)):
    return update_event_status_service(db, event_id, "archived", current_user)


@router.get("/{event_id}/admin-notes", summary="View Enterprise Admin Notes on Event")
def get_admin_notes(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_event_manager)):
    """Enterprise Admin / Provider can see the Enterprise Admin's latest reject/request-changes message (testing as Super Admin)."""
    from app.repository.event_repo import get_event_by_id
    from fastapi import HTTPException
    event = get_event_by_id(db, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return {
        "event_id": str(event.id),
        "title": event.title,
        "status": event.status,
        "last_admin_notes": event.last_admin_notes,
    }


@router.post("/{event_id}/resubmit", response_model=EventResponse, status_code=status.HTTP_200_OK, summary="Resubmit Event After Revision")
def resubmit_event(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_event_manager)):
    """Resubmit event for approval after Enterprise Admin requested changes (needs_revision -> pending_approval) — testing as Super Admin."""
    from app.repository.event_repo import get_event_by_id
    from fastapi import HTTPException
    event = get_event_by_id(db, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.status not in ("needs_revision", "draft"):
        raise HTTPException(status_code=400, detail=f"Cannot resubmit event in '{event.status}' status. Must be needs_revision or draft.")
    return update_event_status_service(db, event_id, "pending_approval", current_user)


# ---- Registrations & Waitlist (E7-E10) ----


@router.get("/{event_id}/registrations", summary="List Registrations")
def list_registrations(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_event_manager)):
    return get_event_registrations_service(db, event_id)


@router.post(
    "/{event_id}/registrations",
    status_code=status.HTTP_201_CREATED,
    summary="Register for Event",
    responses={
        409: {"description": "Already registered", "content": {"application/json": {"example": {"detail": "Already registered for this event."}}}},
    },
)
def register(event_id: UUID, payload: EventRegistrationCreate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return create_registration_service(db, event_id, payload)


@router.delete("/{event_id}/registrations/{reg_id}", summary="Cancel Registration")
def cancel_registration(request: Request, event_id: UUID, reg_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.services.event_service import cancel_registration_service

    return cancel_registration_service(db, event_id, reg_id, current_user, access_token=extract_access_token(request))


@router.get(
    "/{event_id}/registrations/export",
    summary="Export Registrations CSV",
    description=(
        "CSV of the event's registrations. Accepts the same filters as `GET /{event_id}/attendees`. The first five "
        "columns (id, name, email, status, qr_code) are unchanged; ticket type, quantity, payment status, order, "
        "amount, currency, check-in, registration time and the custom answers follow. Text cells that would be "
        "read as spreadsheet formulas are prefixed with an apostrophe."
    ),
)
def export_registrations(
    event_id: UUID,
    filters: AttendeeFilters = Depends(attendee_filters),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_event_staff),
):
    from fastapi.responses import StreamingResponse

    csv_text = export_attendees_csv(db, event_id, filters)
    return StreamingResponse(iter([csv_text]), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=event_{event_id}_registrations.csv"})


@router.get(
    "/{event_id}/registrations/{reg_id}",
    response_model=EventAttendeeResponse,
    summary="Get Attendee (Registration Detail)",
    description="One registration of this event with its derived payment status and custom answers. A registration that belongs to a different event is a 404.",
    responses={404: {"description": "Event or registration not found"}, 403: {"description": "Not the event's owner"}},
)
def get_attendee(
    event_id: UUID,
    reg_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_event_staff),
):
    return get_attendee_service(db, event_id, reg_id)


@router.patch(
    "/{event_id}/registrations/{reg_id}/meals",
    response_model=EventMealSelectionResponse,
    summary="Update a Registration's Meal Selections",
    description=(
        "Replaces the meals selected on ONE registration of this event (an empty list clears them). Allowed for the participant "
        "themself (their email matches the registration's, case-insensitively) and for the event's owner (admin/provider of the "
        "owning tenant, or an active platform super admin); a registration id alone grants nothing. Every id must be an ACTIVE "
        "option of the event's meals configuration (an option the attendee already holds may be kept after it is retired), and "
        "meals must be enabled (modules.meals). Only an active registration of an open event can change its meals. "
        "Payment, capacity and the waitlist are not touched."
    ),
    responses={
        400: {"description": "Registration cancelled/inactive or event closed"},
        403: {"description": "Neither the participant nor the event's owner"},
        404: {"description": "Event or registration (of this event) not found"},
        422: {"description": "Meals disabled, unknown or retired meal option, duplicate or malformed selection"},
    },
)
def update_registration_meals(
    request: Request,
    event_id: UUID,
    reg_id: UUID,
    payload: EventMealSelectionUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return update_registration_meals_service(db, event_id, reg_id, payload, current_user, access_token=extract_access_token(request))


@router.get(
    "/{event_id}/attendees",
    response_model=EventAttendeePaginatedResponse,
    summary="List Attendees",
    description=(
        "Paginated attendee list for event managers: search (`q`), filters (status, ticket type, payment status, "
        "checked-in, registration date) and sorting. `payment_status` is derived from the paired order and is never "
        "stored on the registration. The legacy `GET /{event_id}/registrations` (bare array) is unchanged."
    ),
    responses={403: {"description": "Not the event's owner"}, 404: {"description": "Event not found"}},
)
def list_attendees(
    event_id: UUID,
    filters: AttendeeFilters = Depends(attendee_filters),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_event_staff),
):
    return list_attendees_service(db, event_id, filters, page, page_size)


@router.get(
    "/{event_id}/dashboard",
    response_model=EventDashboardResponse,
    summary="Event Dashboard",
    description=(
        "Read-only operational snapshot: registrations, capacity, attendance, waitlist, orders and revenue. "
        "Revenue comes from orders only; capacity uses the same seat accounting as registration and checkout."
    ),
    responses={403: {"description": "Not the event's owner"}, 404: {"description": "Event not found"}},
)
def event_dashboard(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_event_staff)):
    return get_event_dashboard_service(db, event_id)


@router.post(
    "/{event_id}/walk-in",
    response_model=EventWalkInResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Walk-in Registration (organizer)",
    description=(
        "An organizer registers, and by default checks in, an attendee at the venue. Organizer only: the owning "
        "admin/provider or an active platform super admin; customers cannot call it. The result is a normal "
        "registration (same QR, same attendee list and dashboard) with `registration_source: walk_in`. "
        "The public registration window is NOT applied; the event must be published and not finished, cancelled or suspended. "
        "Capacity is enforced with the Phase 2.1 seat accounting plus live waitlist offers: a full event is a 400, and a walk-in "
        "never joins, promotes or consumes the waitlist. One active registration per event and email (409, as online). "
        "Free event: registered and checked in. Priced ticket: registered with a **pending** payment and NOT checked in "
        "(`check_in.reason: payment_pending`); the backend has no offline/manual payment mode, so no payment is ever faked. "
        "`session_id` additionally checks the attendee in to that one session; no other session is touched. "
        "Registration, order, check-in(s) and audit rows are committed together or not at all."
    ),
    responses={
        400: {"description": "Event closed/not published/finished, event or ticket type full, invalid session, invalid ticket price, or an invalid/missing registration-form answer"},
        403: {"description": "Not the event's owner"},
        404: {"description": "Event or ticket type not found"},
        409: {"description": "The participant already has an active registration for this event"},
    },
)
def walk_in_registration(
    request: Request,
    event_id: UUID,
    payload: EventWalkInRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_event_staff),
):
    result = create_walk_in_service(db, event_id, payload, current_user)
    result.ticket.qr_image_path = request.app.url_path_for("get_qr_image", event_id=event_id, reg_id=result.registration.registration_id)
    return result


@router.post("/{event_id}/checkout", response_model=EventOrderResponse, status_code=status.HTTP_201_CREATED, summary="Checkout — Paid Registration")
def checkout(event_id: UUID, payload: EventCheckoutRequest, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return create_event_checkout_service(db, event_id, payload)


@router.get("/{event_id}/orders", summary="List Orders")
def list_orders(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_event_manager)):
    return get_event_orders_service(db, event_id)


@router.post("/{event_id}/registrations/{reg_id}/refund", summary="Request Refund")
def refund_registration(request: Request, event_id: UUID, reg_id: UUID, payload: EventRefundRequest | None = None, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return create_event_refund_service(db, event_id, reg_id, payload, current_user=current_user, access_token=extract_access_token(request))


@router.post("/{event_id}/orders/{order_id}/refund", summary="Refund Order")
def refund_order(event_id: UUID, order_id: UUID, payload: EventRefundRequest | None = None, db: Session = Depends(get_db), current_user: dict = Depends(require_event_manager)):
    return create_event_refund_service(db, event_id, order_id, payload, current_user=current_user, staff_verified=True)


@router.get("/{event_id}/waitlist", summary="List Waitlist")
def list_waitlist(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_event_manager)):
    return get_event_waitlist_service(db, event_id)


@router.post(
    "/{event_id}/waitlist",
    status_code=status.HTTP_201_CREATED,
    summary="Join Waitlist",
    responses={
        409: {"description": "Already on waitlist", "content": {"application/json": {"example": {"detail": "Already on the waitlist for this event."}}}},
    },
)
def join_waitlist(event_id: UUID, payload: EventRegistrationCreate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return create_waitlist_entry_service(db, event_id, payload)


@router.delete("/{event_id}/waitlist/{entry_id}", summary="Leave Waitlist")
def leave_waitlist(request: Request, event_id: UUID, entry_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return delete_waitlist_entry_service(db, event_id, entry_id, current_user, access_token=extract_access_token(request))


# ---- Sessions & Attendance (E11-E12) ----


@router.get("/{event_id}/sessions", summary="List Sessions")
def list_sessions(request: Request, event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return get_sessions_service(db, event_id, current_user=current_user, access_token=extract_access_token(request))


@router.post("/{event_id}/sessions", status_code=status.HTTP_201_CREATED, summary="Add Session")
def add_session(event_id: UUID, payload: EventSessionCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_event_manager)):
    return add_session_service(db, event_id, payload)


@router.put("/{event_id}/sessions/{session_id}", summary="Update Session")
def update_session(event_id: UUID, session_id: str, payload: EventSessionUpdate, db: Session = Depends(get_db), current_user: dict = Depends(require_event_manager)):
    return update_session_service(db, event_id, session_id, payload)


@router.delete("/{event_id}/sessions/{session_id}", summary="Delete Session")
def delete_session(event_id: UUID, session_id: str, db: Session = Depends(get_db), current_user: dict = Depends(require_event_manager)):
    return delete_session_service(db, event_id, session_id)


# Session-level attendance (Phase 2.4). Additive: event-level check-in below is unchanged, and none of these
# routes changes the registration status, capacity, payment or waitlist. Same staff dependency as the
# attendee/dashboard routes: the owning admin/provider or an active platform super admin.

_SESSION_ATTENDANCE_ERRORS = {
    400: {"description": "Not eligible: event cancelled/completed/archived/suspended, registration cancelled or refunded, or not checked in"},
    403: {"description": "Not the event's owner"},
    404: {"description": "Event, session (must be one of this event's sessions) or registration (must belong to this event) not found"},
}


@router.post(
    "/{event_id}/sessions/{session_id}/check-in",
    response_model=EventSessionAttendanceResponse,
    summary="Session Check-in",
    description=(
        "Check an attendee in to one session, by registration id or by their existing registration QR value. "
        "Independent per session and of event-level check-in, which it does not change. A repeat check-in is a "
        "200 with `outcome: already_checked_in` and changes nothing."
    ),
    responses=_SESSION_ATTENDANCE_ERRORS,
)
def session_check_in(
    event_id: UUID,
    payload: EventSessionCheckInRequest,
    session_id: str = Path(..., max_length=100, description="Session id inside the event's sessions"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_event_staff),
):
    return check_in_session_service(db, event_id, session_id, payload, current_user)


@router.post(
    "/{event_id}/sessions/{session_id}/uncheck-in",
    response_model=EventSessionAttendanceResponse,
    summary="Undo Session Check-in",
    description="Removes the attendee's attendance for this session only (history stays in the audit trail). Event-level check-in is untouched.",
    responses=_SESSION_ATTENDANCE_ERRORS,
)
def session_uncheck_in(
    event_id: UUID,
    payload: EventSessionUncheckInRequest,
    session_id: str = Path(..., max_length=100, description="Session id inside the event's sessions"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_event_staff),
):
    return uncheck_in_session_service(db, event_id, session_id, payload, current_user)


@router.post(
    "/{event_id}/sessions/{session_id}/check-out",
    response_model=EventSessionAttendanceResponse,
    summary="Session Check-out",
    description="Records that the attendee left this session. Needs a prior session check-in; a repeat is `already_checked_out`.",
    responses=_SESSION_ATTENDANCE_ERRORS,
)
def session_check_out(
    event_id: UUID,
    payload: EventSessionCheckOutRequest,
    session_id: str = Path(..., max_length=100, description="Session id inside the event's sessions"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_event_staff),
):
    return check_out_session_service(db, event_id, session_id, payload, current_user)


@router.post(
    "/{event_id}/sessions/{session_id}/batch-check-in",
    response_model=EventSessionBatchCheckInResponse,
    status_code=status.HTTP_200_OK,
    summary="Session Batch Check-in",
    description=(
        "Check several attendees in to one session. Each entry (registration id or QR value) is validated on its "
        "own: one that is unknown, from another event, cancelled or refunded fails alone and never blocks the "
        "others. The existing `POST /{event_id}/batch-check-in` (event-level) is unchanged."
    ),
    responses=_SESSION_ATTENDANCE_ERRORS,
)
def session_batch_check_in(
    event_id: UUID,
    payload: EventSessionBatchCheckInRequest,
    session_id: str = Path(..., max_length=100, description="Session id inside the event's sessions"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_event_staff),
):
    return batch_check_in_session_service(db, event_id, session_id, payload.participants, current_user)


@router.post("/{event_id}/check-in", summary="Check-in Participant")
def check_in(event_id: UUID, payload: EventCheckInRequest, db: Session = Depends(get_db), current_user: dict = Depends(require_event_manager)):
    return check_in_service(db, event_id, payload, current_user)


@router.post("/{event_id}/uncheck-in", summary="Undo Check-in")
def uncheck_in(event_id: UUID, payload: EventUncheckInRequest, db: Session = Depends(get_db), current_user: dict = Depends(require_event_manager)):
    return uncheck_in_service(db, event_id, payload, current_user)


@router.post("/{event_id}/check-out", summary="Check-out Participant")
def check_out(event_id: UUID, payload: EventCheckOutRequest, db: Session = Depends(get_db), current_user: dict = Depends(require_event_manager)):
    return check_out_service(db, event_id, payload, current_user)


@router.post("/{event_id}/validate-qr", summary="Validate QR Code")
def validate_qr(event_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_event_manager)):
    return validate_qr_service(db, event_id, payload.get("qr_code"))


@router.get("/{event_id}/registrations/{reg_id}/qr", summary="Get QR Code Image")
def get_qr_image(request: Request, event_id: UUID, reg_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.models.event_aux_models import EventRegistration
    from fastapi import HTTPException
    from fastapi.responses import StreamingResponse
    import io

    reg = db.query(EventRegistration).filter(
        EventRegistration.id == reg_id,
        EventRegistration.event_id == event_id
    ).first()
    if not reg:
        raise HTTPException(status_code=404, detail="Registration not found")
        
    # IDOR: the participant themself (case-insensitive email), or staff who own the Event's tenant.
    email = (current_user.get("email") or "").strip().lower()
    is_owner = bool(reg.participant_email and email and reg.participant_email.strip().lower() == email)
    if not is_owner:
        if current_user.get("role") not in (*STAFF_ROLES, "super_admin"):
            raise HTTPException(status_code=403, detail="Not authorized to view this QR code")
        from app.repository.event_repo import get_event_by_id
        ev = get_event_by_id(db, event_id)
        if ev is None:
            raise HTTPException(status_code=404, detail="Event not found")
        assert_event_access(db, ev, current_user, access_token=extract_access_token(request))

    try:
        import qrcode
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(reg.qr_code)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return StreamingResponse(buf, media_type="image/png", headers={"Content-Disposition": f"inline; filename=qr_{reg.qr_code}.png"})
    except ImportError:
        raise HTTPException(status_code=500, detail="QR code generation library is not installed on the server.")


@router.get("/{event_id}/calendar.ics", summary="Add to calendar — Event + Sessions (ICS)")
def event_calendar(request: Request, event_id: UUID, db: Session = Depends(get_db), current_user: dict | None = Depends(get_optional_current_user)):
    from fastapi.responses import Response
    from app.services.calendar_service import event_to_ics
    from app.repository.event_repo import get_event_by_id
    from fastapi import HTTPException
    ev = get_event_by_id(db, event_id)
    token = extract_access_token(request)
    if not ev or not can_view_event(db, ev, build_event_viewer(db, current_user, access_token=token), access_token=token):
        raise HTTPException(status_code=404, detail="Event not found")
    ics = event_to_ics(ev)
    return Response(content=ics, media_type="text/calendar", headers={"Content-Disposition": f"attachment; filename=event_{event_id}.ics"})


@router.get("/{event_id}/sessions/{session_id}/calendar.ics", summary="Add to calendar — Single Session (ICS)")
def session_calendar(request: Request, event_id: UUID, session_id: str, db: Session = Depends(get_db), current_user: dict | None = Depends(get_optional_current_user)):
    from fastapi.responses import Response
    from app.services.calendar_service import event_to_ics
    from app.repository.event_repo import get_event_by_id
    from fastapi import HTTPException
    ev = get_event_by_id(db, event_id)
    token = extract_access_token(request)
    if not ev or not can_view_event(db, ev, build_event_viewer(db, current_user, access_token=token), access_token=token):
        raise HTTPException(status_code=404, detail="Event not found")
    sess = next((s for s in (ev.sessions or []) if isinstance(s, dict) and s.get("id")==session_id), None)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    ics = event_to_ics(ev, sessions=[sess])
    return Response(content=ics, media_type="text/calendar", headers={"Content-Disposition": f"attachment; filename=event_{event_id}_session_{session_id}.ics"})


@router.get("/{event_id}/meeting-link", summary="Get Meeting Link (registered only)")
def get_meeting_link(request: Request, event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return get_meeting_link_service(db, event_id, current_user, access_token=extract_access_token(request))


@router.get("/{event_id}/sessions/{session_id}/meeting-link", summary="Get Session Meeting Link (registered only)")
def get_session_meeting_link(request: Request, event_id: UUID, session_id: str, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    from app.services.event_service import get_session_meeting_link_service
    return get_session_meeting_link_service(db, event_id, session_id, current_user, access_token=extract_access_token(request))


@router.post("/{event_id}/contact", summary="Contact Organiser")
def contact_organiser(event_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return contact_organiser_service(db, event_id, payload, current_user)


@router.get("/{event_id}/attendance", summary="Attendance Report")
def attendance(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_event_manager)):
    return get_event_attendance_service(db, event_id)


# ---- Announcements, Feedback, Reviews, Reports, Templates (E13-E16) ----


@router.post("/{event_id}/announcements", status_code=status.HTTP_201_CREATED, summary="Send Announcement")
def announce(event_id: UUID, payload: EventAnnouncementCreate, db: Session = Depends(get_db), current_user: dict = Depends(require_event_manager)):
    return send_announcement_service(db, event_id, payload, current_user)


@router.post("/{event_id}/remind", status_code=status.HTTP_201_CREATED, summary="Send Event Reminder")
def remind(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_event_manager)):
    from app.repository.event_repo import get_event_by_id
    from fastapi import HTTPException
    ev = get_event_by_id(db, event_id)
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
    from app.services.notification_triggers import notify_event_reminder
    notify_event_reminder(db, ev)
    return {"message": "Reminders sent", "event_id": str(event_id)}


@router.post("/{event_id}/feedback", status_code=status.HTTP_201_CREATED, summary="Submit Feedback")
def submit_feedback(event_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return create_feedback_service(db, event_id, payload, is_review=False)


@router.get("/{event_id}/feedback", summary="List Feedback")
def list_feedback(event_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_event_manager)):
    return get_event_feedbacks_service(db, event_id, is_review=False)


@router.post("/{event_id}/reviews", status_code=status.HTTP_201_CREATED, summary="Submit Review")
def submit_review(event_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    return create_feedback_service(db, event_id, payload, is_review=True)


@router.patch("/{event_id}/reviews/{review_id}/moderate", summary="Moderate Review")
def moderate_review(event_id: UUID, review_id: UUID, payload: dict, db: Session = Depends(get_db), current_user: dict = Depends(require_event_admin)):
    return moderate_review_service(db, review_id, payload.get("action", "approved"), event_id=event_id)


@router.get("/{event_id}/reports", summary="Event Reports")
def reports(event_id: UUID, type: str = Query("registration"), format: str = Query("json"), db: Session = Depends(get_db), current_user: dict = Depends(require_event_manager)):
    return get_event_reports_service(db, event_id, type)


@router.get("/reports/summary", summary="Performance Dashboard")
def reports_summary(request: Request, enterprise_id: UUID | None = None, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    # Scope always comes from the authenticated caller, never from a query parameter.
    tenant_id = resolve_caller_tenant_id(db, current_user, access_token=extract_access_token(request))
    if not tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant could not be resolved for this caller")
    return get_event_summary_service(db, enterprise_id, tenant_id=tenant_id)


@router.put(
    "/templates/{template_id}",
    response_model=EventTemplateResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Template",
    description="Partial update — send only `name` and/or `template_data`. `enterprise_id` cannot be changed after creation. Returns full EventTemplate.",
    responses={
        404: {"description": "Template not found", "content": {"application/json": {"example": {"detail": "Template not found"}}}},
        403: {"description": "Tenant mismatch", "content": {"application/json": {"example": {"detail": "Template does not belong to your tenant"}}}},
    },
)
def update_template(request: Request, template_id: UUID, payload: EventTemplateUpdateRequest, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.services.event_service import update_template_service
    return update_template_service(db, template_id, payload.model_dump(exclude_unset=True), current_user, access_token=extract_access_token(request))


@router.delete(
    "/templates/{template_id}",
    response_model=EventTemplateDeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete Template",
    description="Delete a template. Empty body. Always succeeds if template exists and belongs to tenant; no 409 conflict (no usage check).",
    responses={
        404: {"description": "Template not found", "content": {"application/json": {"example": {"detail": "Template not found"}}}},
        403: {"description": "Tenant mismatch", "content": {"application/json": {"example": {"detail": "Template does not belong to your tenant"}}}},
    },
)
def delete_template(request: Request, template_id: UUID, db: Session = Depends(get_db), current_user: dict = Depends(require_roles(["admin", "provider"]))):
    from app.services.event_service import delete_template_service
    return delete_template_service(db, template_id, current_user, access_token=extract_access_token(request))


@router.get(
    "/{event_id}/batch-check-in",
    response_model=list[EventBatchCheckInPreviewItem],
    summary="Batch Check-in — List registrations for scanning",
    description="Returns registrations for the event with eligibility computed server-side. Frontend renders table: pick multi + POST /batch-check-in + refresh attendance.",
)
def batch_check_in_preview(event_id: UUID, status_filter: str | None = Query(None, alias="status", description="Filter: confirmed|attended|cancelled"), db: Session = Depends(get_db), current_user: dict = Depends(require_event_manager)):
    from app.models.event_aux_models import EventRegistration
    from app.services.event_service import _get_event_or_404
    _get_event_or_404(db, event_id)
    q = db.query(EventRegistration).filter(EventRegistration.event_id == event_id)
    if status_filter:
        q = q.filter(EventRegistration.status == status_filter)
    regs = q.order_by(EventRegistration.participant_name).all()
    out: list[dict] = []
    for r in regs:
        can = r.status == "confirmed"
        if r.status == "attended":
            reason = "Already checked in"
        elif r.status == "cancelled":
            reason = "Cancelled — cannot check in"
        elif r.status == "no_show":
            reason = "Marked no-show"
        elif can:
            reason = "Ready to check in"
        else:
            reason = f"Status {r.status}"
        out.append(
            {
                "registration_id": r.id,
                "participant_name": r.participant_name,
                "participant_email": r.participant_email,
                "status": r.status,
                "qr_code": r.qr_code,
                "session_id": r.session_id,
                "ticket_type_id": r.ticket_type_id,
                "checked_in_at": r.checked_in_at.isoformat() if r.checked_in_at else None,
                "checked_out_at": r.checked_out_at.isoformat() if r.checked_out_at else None,
                "can_check_in": can,
                "eligibility_reason": reason,
            }
        )
    return out


@router.post(
    "/{event_id}/batch-check-in",
    response_model=EventBatchCheckInResponse,
    status_code=status.HTTP_200_OK,
    summary="Batch Check-in — Check in multiple participants",
    description="Batch check-in: body { participants: [{registration_id|qr_code, session_id?}] }. Returns {total, succeeded, failed, results:[{registration_id, participant_name, status, checked_in_at, message}]} — refresh GET /batch-check-in or GET /{id}/attendance after.",
)
def batch_check_in(event_id: UUID, payload: EventBatchCheckInRequest, db: Session = Depends(get_db), current_user: dict = Depends(require_event_manager)):
    from app.services.event_service import batch_checkin_service
    return batch_checkin_service(db, event_id, payload.participants, current_user)


@router.post("/auto-complete", summary="Auto-complete past published events", status_code=status.HTTP_200_OK)
def auto_complete_events(request: Request, enterprise_id: UUID | None = None, db: Session = Depends(get_db), current_user: dict = Depends(get_current_admin)):
    from app.services.event_service import auto_complete_past_events_service
    tenant_id = None
    if not is_platform_super_admin(current_user):
        # A tenant-scoped admin may only complete its own tenant's events.
        tenant_id = resolve_caller_tenant_id(db, current_user, access_token=extract_access_token(request))
        if not tenant_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant could not be resolved for this caller")
    return auto_complete_past_events_service(db, enterprise_id, tenant_id=tenant_id, current_user=current_user)





# ---- Event Order Status & Refund Approval ----

from app.schemas.event_schema import EventOrderStatusUpdate, EventRefundApproveRequest
from app.services.event_service import update_event_order_status_service, approve_event_refund_service


@router.patch(
    "/{event_id}/orders/{order_id}/status",
    summary="Update Event Order Status (Admin/Provider)",
)
def update_event_order_status(
    event_id: UUID,
    order_id: UUID,
    payload: EventOrderStatusUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_event_manager),
):
    return update_event_order_status_service(db, event_id, order_id, payload, current_user=current_user)


@router.post(
    "/{event_id}/orders/{order_id}/refund/approve",
    summary="Approve or Reject Event Refund (Admin/Provider)",
)
def approve_event_refund(
    event_id: UUID,
    order_id: UUID,
    payload: EventRefundApproveRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_event_manager),
):
    return approve_event_refund_service(db, event_id, order_id, payload, current_user=current_user)
