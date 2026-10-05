import logging
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.models.enterprise_model import Enterprise
from app.models.location_model import EnterpriseLocation
from fastapi.encoders import jsonable_encoder

from app.repository.event_repo import (
    STAFF_ROLES,
    EventViewer,
    assert_event_access,
    build_event_viewer,
    can_view_event,
    create_event,
    delete_event,
    event_owner_tenant_id,
    event_tenant_clause,
    get_event_by_id,
    get_events,
    is_platform_super_admin,
    resolve_caller_tenant_id,
    update_event,
    viewer_owns_event,
)
from app.repository.query_utils import build_pagination_meta
from app.schemas.event_schema import (
    EventDetailResponse,
    EventListItemResponse,
    EventPaginatedResponse,
    EventResponse,
)
from app.services.response_mappers import map_event_detail, map_event_list_item, map_event_write
from app.utils.event_accommodation import (
    AccommodationConfigError,
    accommodation_for_new_event,
    plan_accommodation_update,
)
from app.utils.event_meals import MealConfigError, meals_for_new_event, plan_meals_update
from app.utils.event_modules import (
    EventModuleConfigError,
    is_paid_event,
    legacy_modules,
    modules_for_new_event,
    plan_config_update,
    resolve_event_modules,
    validate_module_overrides,
)


def _validate_references(db: Session, enterprise_id: UUID | None, location_id: UUID | None, current_user: dict | None = None):
    if not enterprise_id:
        # Tenant-owned event — validate location_id independently if supplied (non-breaking, previously skipped)
        if location_id:
            location = (
                db.query(EnterpriseLocation)
                .filter(
                    EnterpriseLocation.id == location_id,
                    EnterpriseLocation.is_deleted.is_(False),
                )
                .first()
            )
            if not location:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
        return
    enterprise = (
        db.query(Enterprise)
        .filter(Enterprise.id == enterprise_id, Enterprise.is_deleted.is_(False))
        .first()
    )
    if not enterprise:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Enterprise not found")
    # Tenant ownership: non-admin must own enterprise tenant
    if current_user and current_user.get("role") not in ("admin", "super_admin"):
        user_tid = current_user.get("tenant_id")
        if user_tid and str(enterprise.tenant_id) != str(user_tid):
            raise HTTPException(status_code=403, detail="Not authorized for this enterprise/tenant")
    # Must be under approved business/profile
    if enterprise.status in ("draft", "pending", "inactive"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Enterprise not approved (status={enterprise.status}). Events can only be created under an approved business/profile.",
        )

    if location_id:
        location = (
            db.query(EnterpriseLocation)
            .filter(
                EnterpriseLocation.id == location_id,
                EnterpriseLocation.enterprise_id == enterprise_id,
                EnterpriseLocation.is_deleted.is_(False),
            )
            .first()
        )
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Location not found for this enterprise"
            )


def _auto_meeting_link(delivery_mode: str | None, provider: str | None, existing: str | None) -> str | None:
    if existing:
        return existing
    if delivery_mode in ("online", "hybrid") and provider:
        import uuid as _u, random, string
        p = (provider or "").lower()
        if p == "zoom":
            return f"https://zoom.us/j/{random.randint(1000000000, 9999999999)}"
        if p == "google_meet":
            code = "".join(random.choices(string.ascii_lowercase, k=3)) + "-" + "".join(random.choices(string.ascii_lowercase, k=4)) + "-" + "".join(random.choices(string.ascii_lowercase, k=3))
            return f"https://meet.google.com/{code}"
        if p == "teams":
            return f"https://teams.microsoft.com/l/meetup-join/{_u.uuid4()}"
        return f"https://meet.example.com/{_u.uuid4()}"
    return existing

def _check_category(db: Session, category: str | None, subcategory: str | None):
    if not category and not subcategory:
        return
    from app.models.event_aux_models import EventCategory
    # If any categories exist, enforce that provided category must match an existing one
    count = db.query(EventCategory).count()
    if count == 0:
        return  # no admin categories yet, allow free-text
    if category:
        cat = db.query(EventCategory).filter(EventCategory.name == category).first()
        if not cat:
            raise HTTPException(status_code=400, detail=f"Category '{category}' not found in admin-managed categories")
    if subcategory and category:
        parent = db.query(EventCategory).filter(EventCategory.name == category).first()
        if parent:
            sub = db.query(EventCategory).filter(EventCategory.name == subcategory, EventCategory.parent_id == parent.id).first()
            if not sub:
                raise HTTPException(status_code=400, detail=f"Subcategory '{subcategory}' not found under '{category}'")

def _actor_id(current_user: dict | None) -> str | None:
    """Audit actor: the authenticated user's id (None for system/unauthenticated callers)."""
    if not current_user:
        return None
    value = current_user.get("id")
    return str(value)[:255] if value else None


def _json_safe(value):
    """Datetimes/UUIDs/Decimals -> JSON-native values so an audit row can never fail to serialize."""
    if value is None:
        return None
    try:
        return jsonable_encoder(value)
    except Exception:
        return str(value)


def _log_audit(
    db: Session,
    event_id: UUID,
    action: str,
    before: dict | None,
    after: dict | None,
    changed_by: str | None = None,
    notes: str | None = None,
    commit: bool = True,
):
    """Record an EventAudit row.

    ``commit=False`` only stages the row in the caller's transaction (it is committed, or
    rolled back, together with the change it describes) — use it for every write path so
    no audit call ever commits a half-finished unit of work. ``commit=True`` is kept for
    the legacy call shape: it commits the audit row on its own and never raises.
    """
    try:
        from app.models.event_aux_models import EventAudit
        audit = EventAudit(
            event_id=event_id,
            action=action,
            before=_json_safe(before),
            after=_json_safe(after),
            changed_by=changed_by,
            notes=notes,
        )
        db.add(audit)
        if commit:
            db.commit()
        return audit
    except Exception:
        logger.warning("Failed to record audit action %s for event %s", action, event_id, exc_info=True)
        if commit:
            try:
                db.rollback()
            except Exception:
                pass
        return None

def _validate_event_config_for_create(event_data) -> None:
    """422 for contradictory *explicit* module overrides on a new event.

    Only what the client asked for is checked. Values that come from an event-type default are a
    starting point, not a request, so a default can never make a create fail. Stored configuration is
    never re-validated this way (validate_event_submission only checks its shape), so a valid stored
    default can never block publishing. Event-Type-specific allowed/required checks happen later, once
    the Event Type has been resolved from the database (see _resolve_event_type_or_422).
    """
    overrides = event_data.modules.overrides() if getattr(event_data, "modules", None) is not None else {}
    if not overrides:
        return
    is_paid = is_paid_event(event_data.pricing_type, event_data.price, event_data._normalize_ticket_types())
    try:
        validate_module_overrides(overrides, delivery_mode=event_data.delivery_mode, is_paid=is_paid)
    except EventModuleConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _event_type_record(row) -> dict:
    """An EventTypeConfig row as the plain dict app.utils.event_modules expects.

    ``registration`` is forced True in all three maps regardless of what is stored: registration is
    mandatory for every Event Type (product rule), and this is the read-side resolution that makes that
    true even for rows seeded before the rule existed (their ``required_modules.registration`` is
    False in the database) — no migration needed, same "resolve on read" pattern as ``legacy_modules``.
    """
    default_modules = dict(row.default_modules)
    allowed_modules = dict(row.allowed_modules)
    required_modules = dict(row.required_modules)
    default_modules["registration"] = True
    allowed_modules["registration"] = True
    required_modules["registration"] = True
    return {
        "key": row.key,
        "default_modules": default_modules,
        "allowed_modules": allowed_modules,
        "required_modules": required_modules,
    }


def _resolve_event_type_or_422(db: Session, key: str | None) -> dict | None:
    """The resolved Event Type record for ``key`` (None = no type: the event stays legacy).

    422 when the key does not name an existing, ACTIVE Event Type — a client can never silently create
    or move an event onto an unknown or deactivated type. This is the only place event_type is checked
    against the database; app.utils.event_modules stays pure and never queries it.
    """
    if key is None:
        return None
    from app.models.event_type_model import EventTypeConfig

    row = db.query(EventTypeConfig).filter(EventTypeConfig.key == key).first()
    if row is None or not row.active:
        raise HTTPException(status_code=422, detail=f"Unknown or inactive event type: {key}")
    return _event_type_record(row)


def _plan_event_config_update(db: Session, event, update_data) -> dict:
    """The event_type / modules columns an update changes (empty when it does not touch configuration).

    Partial-update semantics: null/omitted = no change, so an unrelated update — or a client echoing
    back only what it read — never resets the configuration. See plan_config_update for the rules.
    """
    new_type = getattr(update_data, "event_type", None)
    modules_in = getattr(update_data, "modules", None)
    overrides = modules_in.overrides() if modules_in is not None else {}
    if new_type is None and not overrides:
        return {}
    fields = update_data.model_fields_set
    delivery_mode = update_data.delivery_mode if getattr(update_data, "delivery_mode", None) is not None else event.delivery_mode
    pricing_type = update_data.pricing_type if getattr(update_data, "pricing_type", None) is not None else event.pricing_type
    price = update_data.price if "price" in fields else event.price
    ticket_types = update_data.ticket_types if getattr(update_data, "ticket_types", None) is not None else event.ticket_types
    new_type_record = None
    if new_type is not None and new_type != getattr(event, "event_type", None):
        new_type_record = _resolve_event_type_or_422(db, new_type)
    try:
        return plan_config_update(
            event, new_type, overrides,
            delivery_mode=delivery_mode, is_paid=is_paid_event(pricing_type, price, ticket_types),
            new_event_type_record=new_type_record,
        )
    except EventModuleConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _meals_enabled_flag_matches(meals_in, enabled: bool) -> None:
    """``meals.enabled`` is a mirror of modules.meals, never a second switch: when sent it must agree."""
    if meals_in.enabled is not None and meals_in.enabled != enabled:
        raise HTTPException(
            status_code=422,
            detail=f"meals.enabled ({str(meals_in.enabled).lower()}) conflicts with modules.meals ({str(enabled).lower()}). "
                   "Meals are switched on or off with modules.meals only.",
        )


def _meals_for_new_event(event_data, modules) -> dict | None:
    """The ``meals`` value for a new event (None = nothing configured); 422 when meals are off or the options are invalid."""
    meals_in = getattr(event_data, "meals", None)
    if meals_in is None:
        return None
    enabled = bool((modules if isinstance(modules, dict) else legacy_modules(event_data)).get("meals"))
    _meals_enabled_flag_matches(meals_in, enabled)
    try:
        return meals_for_new_event(meals_in.option_dicts(), enabled=enabled)
    except MealConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _plan_event_meals_update(event, update_data, config_changes: dict) -> dict:
    """The ``meals`` column an update changes (empty when it does not touch meals).

    null/omitted = no change, so an unrelated update — or one that only changes modules — keeps the meal options and
    their ids. Meals are judged against the modules AS THEY WILL BE after this same update.
    """
    meals_in = getattr(update_data, "meals", None)
    if meals_in is None:
        return {}
    modules_after = config_changes["modules"] if isinstance(config_changes.get("modules"), dict) else resolve_event_modules(event)
    enabled_after = bool(modules_after.get("meals"))
    _meals_enabled_flag_matches(meals_in, enabled_after)
    try:
        stored = plan_meals_update(event, meals_in.option_dicts(), enabled_after=enabled_after)
    except MealConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {} if stored is None else {"meals": stored}


def _accommodation_enabled_flag_matches(accommodation_in, enabled: bool) -> None:
    """``accommodation.enabled`` is a mirror of modules.accommodation, never a second switch: when sent it must agree."""
    if accommodation_in.enabled is not None and accommodation_in.enabled != enabled:
        raise HTTPException(
            status_code=422,
            detail=f"accommodation.enabled ({str(accommodation_in.enabled).lower()}) conflicts with modules.accommodation "
                   f"({str(enabled).lower()}). Accommodation is switched on or off with modules.accommodation only.",
        )


def _accommodation_for_new_event(event_data, modules) -> dict | None:
    """The ``accommodation`` value for a new event (None = nothing configured); 422 when accommodation is off or the
    options are invalid."""
    accommodation_in = getattr(event_data, "accommodation", None)
    if accommodation_in is None:
        return None
    enabled = bool((modules if isinstance(modules, dict) else legacy_modules(event_data)).get("accommodation"))
    _accommodation_enabled_flag_matches(accommodation_in, enabled)
    try:
        return accommodation_for_new_event(accommodation_in.option_dicts(), enabled=enabled)
    except AccommodationConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _plan_event_accommodation_update(event, update_data, config_changes: dict) -> dict:
    """The ``accommodation`` column an update changes (empty when it does not touch accommodation).

    null/omitted = no change, so an unrelated update — or one that only changes modules — keeps the options and their
    ids. Accommodation is judged against the modules AS THEY WILL BE after this same update.
    """
    accommodation_in = getattr(update_data, "accommodation", None)
    if accommodation_in is None:
        return {}
    modules_after = config_changes["modules"] if isinstance(config_changes.get("modules"), dict) else resolve_event_modules(event)
    enabled_after = bool(modules_after.get("accommodation"))
    _accommodation_enabled_flag_matches(accommodation_in, enabled_after)
    try:
        stored = plan_accommodation_update(event, accommodation_in.option_dicts(), enabled_after=enabled_after)
    except AccommodationConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {} if stored is None else {"accommodation": stored}


def create_event_service(db: Session, event_data, current_user: dict | None = None):
    from app.services.event_form_config_service import apply_form_configuration_to_event_data

    form_meta = apply_form_configuration_to_event_data(db, event_data, current_user or {})
    # Resolve enterprise_id from authenticated tenant if not provided
    if not event_data.enterprise_id and current_user:
        tenant_id = current_user.get("tenant_id") or current_user.get("tenant_id_claim")
        if tenant_id:
            try:
                from app.models.enterprise_model import Enterprise
                ent = db.query(Enterprise).filter(Enterprise.tenant_id == tenant_id).first()
                if ent:
                    event_data.enterprise_id = ent.id
            except Exception:
                pass
    # Soft fallback: coerce status to draft if not allowed on create
    allowed_create_status = ("draft", "pending_approval")
    if event_data.status not in allowed_create_status:
        event_data.status = "draft"
    _validate_references(db, event_data.enterprise_id, event_data.location_id, current_user)
    _check_category(db, getattr(event_data, "category", None), getattr(event_data, "subcategory", None))
    _validate_event_config_for_create(event_data)
    # auto-create meeting link if needed
    if not event_data.meeting_link:
        event_data.meeting_link = _auto_meeting_link(event_data.delivery_mode, event_data.meeting_provider, None)
    payload = event_data.to_model_data()
    event_type_record = _resolve_event_type_or_422(db, payload.get("event_type"))
    is_paid = is_paid_event(event_data.pricing_type, event_data.price, event_data._normalize_ticket_types())
    try:
        payload["modules"] = modules_for_new_event(
            event_data.modules.overrides() if event_data.modules else None, event_data, event_type_record, is_paid=is_paid
        )
    except EventModuleConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    payload["meals"] = _meals_for_new_event(event_data, payload.get("modules"))
    payload["accommodation"] = _accommodation_for_new_event(event_data, payload.get("modules"))
    payload.update(form_meta)
    from app.models.event_model import Event
    created = Event(**payload)
    db.add(created)
    db.flush()  # assigns created.id for the audit row
    audit = _log_audit(db, created.id, "create", None, {"title": created.title, "status": created.status, "form_configuration_version_id": str(created.form_configuration_version_id) if created.form_configuration_version_id else None, "event_type": created.event_type, "modules": created.modules, "meals": created.meals, "accommodation": created.accommodation}, changed_by=_actor_id(current_user), commit=False)
    db.commit()
    db.refresh(created)
    if created.status == "pending_approval":
        try:
            from app.services.event_notification_service import notify_event_approval_workflow
            notify_event_approval_workflow(
                db,
                created,
                previous_status="draft",
                transition_id=getattr(audit, "id", None),
            )
        except Exception:
            logger.exception("event_submitted notification failed for event %s", created.id)
    return EventResponse.model_validate(map_event_write(created))


def get_events_service(
    db: Session,
    *,
    search: str | None = None,
    category: str | None = None,
    tenant_id: UUID | None = None,
    enterprise_id: UUID | None = None,
    location_id: UUID | None = None,
    status_filter: str | None = None,
    delivery_mode: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_price: str | None = None,
    max_price: str | None = None,
    page: int = 1,
    page_size: int = 20,
    viewer: EventViewer | None = None,
) -> EventPaginatedResponse:
    # No viewer == anonymous public reader (published events only). Callers that legitimately
    # widen the view (staff of the owning tenant, Platform Super Admin) pass an explicit viewer.
    viewer = viewer or EventViewer()
    items, total = get_events(
        db,
        viewer=viewer,
        search=search,
        category=category,
        tenant_id=tenant_id,
        enterprise_id=enterprise_id,
        location_id=location_id,
        status=status_filter,
        delivery_mode=delivery_mode,
        date_from=date_from,
        date_to=date_to,
        min_price=min_price,
        max_price=max_price,
        page=page,
        page_size=page_size,
    )
    return EventPaginatedResponse(
        items=[EventListItemResponse.model_validate(_redact_event_data(map_event_list_item(e, db), e, viewer)) for e in items],
        pagination=build_pagination_meta(total, page, page_size),
    )


# Internal review/workflow fields: only the owning tenant (or the platform) may read them.
_OWNER_ONLY_EVENT_FIELDS = ("last_admin_notes", "requires_reapproval")
# Organiser contact details are not published to the public. Any authenticated staff member keeps
# them: an owner whose tenant cannot be resolved at that instant must not be handed null and then
# save it back over the stored value through the edit form (EventUpdate accepts organiser_contact).
_STAFF_ONLY_EVENT_FIELDS = ("organiser_contact",)


def _redact_event_data(data: dict, event, viewer: EventViewer) -> dict:
    """Blank non-public fields for readers who may not see them (response shape is unchanged)."""
    if viewer_owns_event(event, viewer):
        return data
    hidden = list(_OWNER_ONLY_EVENT_FIELDS)
    if viewer.role not in (*STAFF_ROLES, "super_admin"):
        hidden.extend(_STAFF_ONLY_EVENT_FIELDS)
    return {**data, **{field: None for field in hidden if field in data}}


def get_event_service(
    db: Session, event_id: UUID, viewer: EventViewer | None = None,
    access_token: str | None = None, current_user: dict | None = None,
) -> EventDetailResponse:
    viewer = viewer or EventViewer()
    event = get_event_by_id(db, event_id)
    # 404 (not 403) for events the reader may not see, so hidden events are indistinguishable from absent ones.
    if not event or not can_view_event(db, event, viewer, access_token=access_token):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    detail = map_event_detail(event)
    email = (current_user or {}).get("email")
    registration = None
    if email:
        from app.models.event_aux_models import EventRegistration
        registration = db.query(EventRegistration).filter(
            EventRegistration.event_id == event_id,
            EventRegistration.participant_email == email,
        ).order_by(EventRegistration.created_at.desc()).first()
    detail["registration_status"] = registration.status if registration else None
    detail["is_registered"] = bool(registration) and registration.status in ("confirmed", "attended")
    return EventDetailResponse.model_validate(_redact_event_data(detail, event, viewer))


def update_event_service(db: Session, event_id: UUID, update_data, current_user: dict = None):
    event = get_event_by_id(db, event_id, include_deleted=True)
    if not event or event.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    # Block status change via PUT — must use PATCH /status with proper guards
    if getattr(update_data, "status", None) is not None:
        raise HTTPException(status_code=400, detail="Status cannot be changed via PUT. Use PATCH /{event_id}/status.")

    location_id = update_data.location_id if update_data.location_id is not None else event.location_id
    _validate_references(db, event.enterprise_id, location_id, current_user)
    # category validation if provided
    if getattr(update_data, "category", None) is not None or getattr(update_data, "subcategory", None) is not None:
        _check_category(db, getattr(update_data, "category", None) or event.category, getattr(update_data, "subcategory", None))

    # auto-create meeting link on update if delivery_mode/provider changed and link missing
    cur_mode = update_data.delivery_mode if getattr(update_data, "delivery_mode", None) is not None else event.delivery_mode
    cur_provider = update_data.meeting_provider if getattr(update_data, "meeting_provider", None) is not None else event.meeting_provider
    cur_link = update_data.meeting_link if getattr(update_data, "meeting_link", None) is not None else event.meeting_link
    if not cur_link and cur_mode in ("online", "hybrid") and cur_provider:
        auto = _auto_meeting_link(cur_mode, cur_provider, None)
        if hasattr(update_data, "meeting_link"):
            try:
                update_data.meeting_link = auto
            except Exception:
                pass

    # Configuration (event_type / modules) is planned against the CURRENT event before anything is mutated.
    # It is deliberately kept out of old_vals below: that dict also drives schedule-change notifications.
    config_changes = _plan_event_config_update(db, event, update_data)
    config_changes.update(_plan_event_meals_update(event, update_data, config_changes))
    config_changes.update(_plan_event_accommodation_update(event, update_data, config_changes))
    old_config = {key: getattr(event, key) for key in config_changes}

    # capture old values for schedule change detection and audit
    old_vals = {k: getattr(event, k) for k in ["start_date","end_date","venue","meeting_link","time_zone","duration_type","category","subcategory","title","status"] if hasattr(event, k)}
    from app.services.event_form_config_service import apply_form_configuration_to_event_update, validate_form_required_core_fields
    from app.models.event_form_config_model import EventFormConfigurationVersion
    from app.services.event_form_config_service import normalize_sections

    extra = apply_form_configuration_to_event_update(db, event, update_data, current_user or {})
    if event.status != "draft" and extra is None and any(getattr(update_data, f, None) is not None for f in ("title", "description", "category", "start_date", "end_date")):
        version_id = event.form_configuration_version_id
        if version_id:
            version = db.query(EventFormConfigurationVersion).filter(EventFormConfigurationVersion.id == version_id).first()
            if version:
                from types import SimpleNamespace
                merged = {c.key: getattr(event, c.key) for c in event.__table__.columns}
                merged.update(update_data.model_dump(exclude_unset=True))
                validate_form_required_core_fields(SimpleNamespace(model_dump=lambda: merged), normalize_sections(version.sections or [], assign_ids=False))

    target_status = getattr(update_data, "status", None) or event.status
    if target_status in ("pending_approval", "approved", "published"):
        from app.services.event_template_mapping import validate_event_submission
        changes = update_data.to_model_data() if hasattr(update_data, "to_model_data") else update_data.model_dump(exclude_unset=True)
        validate_event_submission(db, event, {**changes, **(extra or {})})
    updated = update_event(db, event, update_data, commit=False)
    if extra:
        for key, val in extra.items():
            setattr(updated, key, val)
    for key, val in config_changes.items():
        setattr(updated, key, val)  # a new dict for modules, so the JSONB change is always detected
    _log_audit(
        db, event.id, "update",
        {**old_vals, **old_config},
        {**{k: getattr(updated, k) for k in old_vals.keys()}, **{k: getattr(updated, k) for k in config_changes}},
        changed_by=_actor_id(current_user), commit=False,
    )
    db.commit()
    db.refresh(updated)
    # detect changes
    changes = {}
    for k, old in old_vals.items():
        new = getattr(updated, k, None)
        if old != new:
            changes[k] = f"{old} -> {new}"
    if changes:
        try:
            from app.services.notification_triggers import notify_schedule_change
            notify_schedule_change(db, updated, changes)
        except Exception:
            pass
    return EventResponse.model_validate(map_event_write(updated))


def delete_event_service(db: Session, event_id: UUID, current_user: dict = None):
    event = get_event_by_id(db, event_id)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    _log_audit(db, event.id, "delete", {"status": event.status}, {"is_deleted": True}, changed_by=_actor_id(current_user), commit=False)
    return delete_event(db, event)


def duplicate_event_service(db: Session, event_id: UUID, current_user: dict = None):
    import copy
    import uuid

    event = get_event_by_id(db, event_id)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    # Clone fields, reset id/status
    payload = {c.key: getattr(event, c.key) for c in event.__table__.columns if c.key not in ("id", "created_at", "updated_at")}
    payload["status"] = "draft"
    payload["is_deleted"] = False
    # Deep copy sessions and regenerate ids so cloned event sessions are independent and addressable
    if payload.get("sessions"):
        cloned_sessions = copy.deepcopy(payload["sessions"])
        for s in cloned_sessions:
            s["id"] = str(uuid.uuid4())
            # normalize session_date to string if it's date object
            sd = s.get("session_date")
            if hasattr(sd, "isoformat"):
                s["session_date"] = sd.isoformat()
        payload["sessions"] = cloned_sessions

    from app.models.event_model import Event

    clone = Event(**payload)
    db.add(clone)
    db.commit()
    db.refresh(clone)
    return EventResponse.model_validate(map_event_write(clone))


def update_event_status_service(db: Session, event_id: UUID, new_status: str, current_user: dict = None, notes: str | None = None):
    event = get_event_by_id(db, event_id, include_deleted=True)
    if not event or event.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    # 1) Provider cannot cancel — only Enterprise Admin may cancel
    if new_status == "cancelled" and current_user and current_user.get("role") == "provider":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Enterprise Admin can cancel events. Provider is assigned host only.",
        )

    # Lifecycle as per spec: pending_approval -> approved -> draft -> published -> completed -> archived
    # Added: needs_revision (super admin asks for changes), rejected (super admin rejects)
    VALID_TRANSITIONS = {
        "pending_approval": ["approved", "rejected", "needs_revision", "cancelled"],
        "approved": ["draft", "published", "cancelled", "archived"],
        "draft": ["published", "pending_approval", "cancelled", "archived"],
        "published": ["completed", "cancelled", "suspended", "approved", "archived"],
        "completed": ["archived"],
        "suspended": ["published", "cancelled", "archived"],
        "cancelled": ["draft", "archived"],
        "archived": [],
        "active": ["cancelled", "completed", "inactive"],
        "inactive": ["active", "cancelled"],
        "needs_revision": ["pending_approval", "cancelled"],  # enterprise edits and resubmits
        "rejected": ["draft", "cancelled"],  # enterprise can revise and resubmit via draft -> pending_approval
    }

    current_status = event.status
    allowed_next = VALID_TRANSITIONS.get(current_status, [])

    if new_status not in allowed_next:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot transition from '{current_status}' to '{new_status}'. Allowed: {allowed_next}"
        )

    # 2) Restored cancelled Event must be re-approved: cancelled→draft sets requires_reapproval,
    # then draft→published is blocked until pending_approval→approved clears it
    if event.requires_reapproval and current_status == "draft" and new_status == "published":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Event was previously cancelled and restored. Must go through draft → pending_approval → approved before publishing. Submit for approval first.",
        )

    if new_status in ("pending_approval", "approved", "published"):
        from app.services.event_template_mapping import validate_event_submission
        validate_event_submission(db, event)

    # Set requires_reapproval when restoring cancelled → draft
    if event.status == "cancelled" and new_status == "draft":
        event.requires_reapproval = True

    # Clear requires_reapproval when Enterprise Admin approves (pending_approval → approved) — admin == Enterprise Admin (no Super Admin)
    if event.requires_reapproval and current_status == "pending_approval" and new_status == "approved":
        event.requires_reapproval = False

    # Store admin notes on event when rejecting or requesting changes
    if new_status in ("rejected", "needs_revision") and notes:
        event.last_admin_notes = notes

    previous = event.status
    event.status = new_status
    audit = _log_audit(db, event.id, "status_change", {"status": previous}, {"status": new_status}, changed_by=_actor_id(current_user), notes=notes, commit=False)
    db.commit()
    db.refresh(event)
    if new_status in ("pending_approval", "approved", "rejected", "needs_revision"):
        try:
            from app.services.event_notification_service import notify_event_approval_workflow
            notify_event_approval_workflow(
                db,
                event,
                previous_status=previous,
                reason=notes,
                transition_id=getattr(audit, "id", None),
            )
        except Exception:
            logger.exception(
                "event workflow notification failed for event %s type=%s",
                event.id,
                new_status,
            )
    if new_status == "cancelled":
        try:
            from app.services.notification_triggers import notify_event_cancelled
            notify_event_cancelled(db, event, previous)
        except Exception:
            pass
    return EventResponse.model_validate(map_event_write(event))


# ---- auxiliary ----

def _get_event_or_404(db: Session, event_id: UUID):
    from app.repository.event_repo import get_event_by_id

    event = get_event_by_id(db, event_id)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return event


# ---- Seat accounting / payment / authorization helpers ----

_ACTIVE_REGISTRATION_STATUSES = ["confirmed", "attended"]


def _to_float(value) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0


def _to_int(value, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _event_is_paid(event) -> bool:
    """A paid event is priced by ``pricing_type``, a top-level ``price`` or any priced ticket type.

    Checking all three matters: events priced only through ``ticket_types`` (or legacy rows whose
    ``pricing_type`` defaulted to "free") must not be treated as free.
    """
    if str(getattr(event, "pricing_type", "") or "").lower() == "paid":
        return True
    if _to_float(getattr(event, "price", None)) > 0:
        return True
    for ticket in getattr(event, "ticket_types", None) or []:
        if isinstance(ticket, dict) and _to_float(ticket.get("price")) > 0:
            return True
    return False


def _seats_taken(db: Session, event_id: UUID, ticket_type_id: str | None = None) -> int:
    """Seats consumed, counting every seat once.

    Each active (confirmed/attended) registration is one seat. A paid checkout creates one order
    AND one registration for the same purchase, so orders are NOT counted on top of registrations
    — only the *extra* seats of a multi-quantity order (an order of N holds N seats but creates a
    single registration) are added.
    """
    from app.models.event_aux_models import EventOrder, EventRegistration

    reg_conditions = [
        EventRegistration.event_id == event_id,
        EventRegistration.status.in_(_ACTIVE_REGISTRATION_STATUSES),
    ]
    order_conditions = [
        EventOrder.event_id == event_id,
        EventOrder.status == "confirmed",
        EventOrder.quantity != "1",
    ]
    if ticket_type_id is not None:
        reg_conditions.append(EventRegistration.ticket_type_id == ticket_type_id)
        order_conditions.append(EventOrder.ticket_type_id == ticket_type_id)
    seats = db.query(EventRegistration).filter(*reg_conditions).count()
    for order in db.query(EventOrder).filter(*order_conditions).all():
        seats += max(_to_int(order.quantity, 1) - 1, 0)
    return seats


def _authorize_participant_or_staff(
    db: Session,
    event,
    participant_email: str | None,
    current_user: dict | None,
    *,
    access_token: str | None = None,
    staff_verified: bool = False,
    forbidden_detail: str = "Not authorized",
) -> None:
    """Allow the participant themself (case-insensitive email), or staff who own the Event's tenant.

    Staff are checked *after* the participant test so a provider who registered for another
    tenant's event can still manage their own registration. A customer can never act on someone
    else's registration/order by knowing its id.
    """
    user_email = ((current_user or {}).get("email") or "").strip().lower()
    if user_email and participant_email and participant_email.strip().lower() == user_email:
        return
    if current_user and current_user.get("role") in STAFF_ROLES:
        if staff_verified:
            return
        if event is None:
            raise HTTPException(status_code=404, detail="Event not found")
        assert_event_access(db, event, current_user, access_token=access_token)
        return
    raise HTTPException(status_code=403, detail=forbidden_detail)


def _linked_order_for_registration(db: Session, reg):
    """The order that paid for ``reg``.

    EventOrder has no registration FK, so use the deterministic pairing: same event and participant
    email (``uq_event_reg_active`` allows at most one active registration per event+email), taking
    the latest order placed at or shortly before the registration was created.
    """
    from datetime import datetime, timedelta

    from app.models.event_aux_models import EventOrder

    cutoff = (reg.created_at or datetime.utcnow()) + timedelta(minutes=5)
    return (
        db.query(EventOrder)
        .filter(
            EventOrder.event_id == reg.event_id,
            sa.func.lower(EventOrder.participant_email) == (reg.participant_email or "").strip().lower(),
            EventOrder.created_at <= cutoff,
        )
        .order_by(EventOrder.created_at.desc())
        .first()
    )


def _active_registration_for_order(db: Session, order):
    """The active registration an order paid for (at most one per event+email by uq_event_reg_active)."""
    from app.models.event_aux_models import EventRegistration

    return (
        db.query(EventRegistration)
        .filter(
            EventRegistration.event_id == order.event_id,
            sa.func.lower(EventRegistration.participant_email) == (order.participant_email or "").strip().lower(),
            EventRegistration.status.in_(_ACTIVE_REGISTRATION_STATUSES),
        )
        .first()
    )


def _cancel_registration_and_release_seat(db: Session, event, reg, *, actor: str | None, audit_action: str, notes: str | None = None) -> None:
    """Make ``reg`` non-active and offer the freed seat to the waitlist. The caller commits.

    Setting the status is what invalidates the QR for check-in and frees the seat (every capacity
    count is over confirmed/attended registrations). The audit row is staged in the same transaction.
    """
    previous = reg.status
    reg.status = "cancelled"
    _log_audit(
        db, event.id, audit_action,
        {"registration_id": str(reg.id), "participant_email": reg.participant_email, "status": previous},
        {"registration_id": str(reg.id), "status": "cancelled"},
        changed_by=actor, notes=notes, commit=False,
    )
    db.flush()  # autoflush is off: the promotion's seat count must see the release
    try:
        with db.begin_nested():  # a promotion failure must not undo the cancellation itself
            _try_promote_from_waitlist(db, event.id, event)
    except Exception:
        logger.exception("Waitlist promotion failed after seat release for event %s", event.id)


def create_registration_service(db: Session, event_id: UUID, payload):
    from datetime import datetime
    from decimal import Decimal
    event = _get_event_or_404(db, event_id)
    from app.models.event_aux_models import EventOrder, EventRegistration
    import uuid

    # Block cancelled/completed/archived/suspended events
    if event.status in ["cancelled", "completed", "archived", "suspended"]:
        raise HTTPException(status_code=400, detail=f"Registrations are closed — event is {event.status}")
    if event.status not in ["published"]:
        raise HTTPException(status_code=400, detail=f"Event not open for registration (status: {event.status})")

    # Registration window enforcement
    from app.utils.event_utils import validate_registration_window
    validate_registration_window(event)

    # Paid events are registered through checkout/payment only. This endpoint must never mint a
    # free confirmed registration (and QR) for an event that charges.
    if _event_is_paid(event):
        raise HTTPException(
            status_code=400,
            detail="This event requires payment. Complete registration through checkout (POST /events/{event_id}/checkout).",
        )

    # Meal/accommodation selections (Phase 2.6/2.7, priced + capacity-checked since Phase 2.8): validated and
    # priced by the SAME shared service checkout uses (event_option_pricing_service.resolve_priced_selections),
    # so a free-ticket registration enforces identical rules — unknown/duplicate/cross-event/inactive/sold-out/
    # out-of-window/mixed-currency — as paid checkout. See create_event_checkout_service for the paid counterpart.
    from app.services.event_option_pricing_service import (
        assert_capacity_available, persist_line_items, resolve_priced_selections,
    )

    requested_meals = getattr(payload, "meal_selections", None)
    requested_accommodation = getattr(payload, "accommodation_selections", None)
    resolved_ticket = _resolve_ticket(event, getattr(payload, "ticket_type_id", None))
    ticket_currency_for_pricing = (
        resolved_ticket.get("currency", event.currency) if isinstance(resolved_ticket, dict) else (event.currency or "INR")
    ) or "INR"
    priced_options = resolve_priced_selections(
        event,
        # Only a real list is a selection: a payload object without the field (older callers, mocks) means "none".
        meal_selections=requested_meals if isinstance(requested_meals, list) else None,
        accommodation_selections=requested_accommodation if isinstance(requested_accommodation, list) else None,
        ticket_currency=ticket_currency_for_pricing,
    )
    meal_selections = [line.option_id for line in priced_options.meal_lines] or None
    accommodation_selections = [line.option_id for line in priced_options.accommodation_lines] or None

    # Group size handling
    group_size = getattr(payload, "group_size", None) or 1
    if group_size < 1:
        group_size = 1

    # --- Phase A: Duplicate registration protection ---
    # Normalise email for comparison — strip + lowercase, matching project convention.
    # Only block if an *active* (confirmed/attended) registration already exists.
    # Cancelled registrations allow re-registration (legitimate product behaviour).
    norm_email = payload.participant_email.strip().lower()
    try:
        # `Event` is not imported at module level; without this local import the NameError was swallowed by
        # the broad except below and the row lock (which serializes the capacity/duplicate checks) never ran.
        from app.models.event_model import Event
        db.query(Event).filter(Event.id == event_id).with_for_update().first()
    except Exception:
        pass
    active_reg = (
        db.query(EventRegistration)
        .filter(
            EventRegistration.event_id == event_id,
            sa.func.lower(EventRegistration.participant_email) == norm_email,
            EventRegistration.status.in_(["confirmed", "attended"]),
        )
        .first()
    )
    if active_reg:
        raise HTTPException(
            status_code=409,
            detail="Already registered for this event. Cancel your existing registration before registering again.",
        )
    # --------------------------------------------------

    # Capacity enforcement (lock already acquired above)
    need = group_size
    if event.capacity:
        try:
            max_capacity = int(float(str(event.capacity).strip()))
            current_count = db.query(EventRegistration).filter(
                EventRegistration.event_id == event_id,
                EventRegistration.status.in_(["confirmed", "attended"])
            ).count()
            if current_count + need > max_capacity:
                raise HTTPException(
                    status_code=400,
                    detail=f"Event is at full capacity ({max_capacity} participants). Only {max_capacity - current_count} seats left. Please join waitlist."
                )
        except ValueError:
            pass
    if getattr(payload, "ticket_type_id", None) and event.ticket_types:
        for t in event.ticket_types or []:
            if isinstance(t, dict) and str(t.get("id")) == str(payload.ticket_type_id) and t.get("capacity"):
                try:
                    cap = int(float(str(t["capacity"]).strip()))
                    cnt = db.query(EventRegistration).filter(EventRegistration.event_id==event_id, EventRegistration.ticket_type_id==payload.ticket_type_id, EventRegistration.status.in_(["confirmed","attended"])).count()
                    if cnt + need > cap:
                        raise HTTPException(status_code=400, detail=f"Ticket type at capacity ({cap})")
                except ValueError:
                    pass
    if event.max_participants:
        try:
            max_p = int(float(str(event.max_participants).strip()))
            current = db.query(EventRegistration).filter(EventRegistration.event_id==event_id, EventRegistration.status.in_(["confirmed","attended"])).count()
            if current + need > max_p:
                raise HTTPException(status_code=400, detail=f"Maximum participants reached ({max_p})")
        except ValueError:
            pass

    # Phase 2.8: meal/accommodation capacity — same (best-effort on SQLite, real on Postgres) row lock as the
    # capacity checks above, so this is serialized against concurrent purchases of the same option identically
    # to tickets.
    assert_capacity_available(db, event, priced_options)

    # Build custom_fields with group info and participant questions
    cf = dict(payload.custom_fields or {})
    if getattr(payload, "group_members", None):
        cf["group_members"] = payload.group_members
    if group_size > 1:
        cf["group_size"] = group_size

    reg = EventRegistration(
        event_id=event_id,
        participant_name=payload.participant_name,
        participant_email=norm_email,
        custom_fields=cf,
        ticket_type_id=payload.ticket_type_id,
        status="confirmed",
        qr_code=str(uuid.uuid4())[:12].upper(),
        meal_selections=meal_selections,
        accommodation_selections=accommodation_selections,
    )
    db.add(reg)
    db.flush()

    # Phase 2.8: "free ≠ zero payable" — a free ticket with paid meal/accommodation selections still needs a
    # real, auditable order (mirrors create_event_checkout_service's EventOrder shape; ticket_subtotal is
    # always 0 here since this endpoint only ever mints free-ticket registrations — see the paid-event guard
    # above). No order at all when every selected option is free too — nothing payable, nothing to audit.
    payable_subtotal = priced_options.meal_subtotal + priced_options.accommodation_subtotal
    order = None
    if payable_subtotal > 0:
        order = EventOrder(
            event_id=event_id,
            participant_name=payload.participant_name,
            participant_email=norm_email,
            ticket_type_id=payload.ticket_type_id,
            quantity="1",
            amount=str(payable_subtotal.quantize(Decimal("0.01"))),
            currency=priced_options.currency or event.currency or "INR",
            payment_status="confirmed",
            status="confirmed",
            payment_provider="marketplace",
            ticket_subtotal="0.00",
            meal_subtotal=str(priced_options.meal_subtotal),
            accommodation_subtotal=str(priced_options.accommodation_subtotal),
        )
        db.add(order)
        db.flush()
    # One immutable snapshot row per selected meal/accommodation option, free or paid (order_id is None for a
    # free-only selection — see the migration's docstring on event_registration_options.order_id).
    persist_line_items(db, event_id=event_id, registration_id=reg.id, order_id=(order.id if order else None), priced=priced_options)

    # Group members: create additional registrations atomically in same transaction
    if getattr(payload, "group_members", None):
        for m in payload.group_members or []:
            try:
                name = m.get("name") or m.get("participant_name") or payload.participant_name
                email = m.get("email") or m.get("participant_email")
                if not email or email.strip().lower() == norm_email:
                    continue
                norm_member_email = email.strip().lower()
                # prevent duplicate email per event (active registrations)
                exists = db.query(EventRegistration).filter(
                    EventRegistration.event_id == event_id,
                    sa.func.lower(EventRegistration.participant_email) == norm_member_email,
                    EventRegistration.status.in_(["confirmed", "attended"]),
                ).first()
                if exists:
                    continue
                extra = EventRegistration(
                    event_id=event_id,
                    participant_name=name,
                    participant_email=norm_member_email,
                    custom_fields={"group_leader": norm_email},
                    ticket_type_id=payload.ticket_type_id,
                    status="confirmed",
                    qr_code=str(uuid.uuid4())[:12].upper(),
                )
                db.add(extra)
            except Exception:
                pass
    db.commit()
    db.refresh(reg)
    # best-effort confirmation notification (in_app, sync, no celery)
    try:
        from app.services.notification_triggers import notify_registration_confirmation
        notify_registration_confirmation(db, event, reg)
    except Exception:
        pass
    return reg


def get_event_registrations_service(db: Session, event_id: UUID):
    _get_event_or_404(db, event_id)
    from app.models.event_aux_models import EventRegistration

    return db.query(EventRegistration).filter(EventRegistration.event_id == event_id).all()


def create_waitlist_entry_service(db: Session, event_id: UUID, payload):
    """Join event waitlist.

    Phase A hardening:
    - Event must be published and registration window must be open.
    - Waitlist is the overflow mechanism — only available when event is at capacity.
    - Duplicate entries (same event_id + normalised email) are rejected.
    """
    from datetime import datetime
    event = _get_event_or_404(db, event_id)
    from app.models.event_aux_models import EventRegistration, EventWaitlist

    # --- Status validation ---
    # Waitlist only makes sense for published events.
    if event.status in ("cancelled", "completed", "archived", "suspended"):
        raise HTTPException(
            status_code=400,
            detail=f"Waitlist closed — event is {event.status}",
        )
    if event.status != "published":
        raise HTTPException(
            status_code=400,
            detail=f"Waitlist is only available for published events (current status: {event.status})",
        )

    # --- Registration window ---
    from app.utils.event_utils import validate_registration_window
    validate_registration_window(event)

    # --- Capacity gate: waitlist is the overflow mechanism ---
    # Only allow joining when the event is full. If there are still open seats
    # the participant should register directly.
    if event.capacity:
        try:
            max_capacity = int(float(str(event.capacity).strip()))
            current_count = _seats_taken(db, event_id)
            if current_count < max_capacity:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Event still has {max_capacity - current_count} seat(s) available. "
                        "Please register directly instead of joining the waitlist."
                    ),
                )
        except ValueError:
            pass  # capacity not parseable — allow waitlist join

    # --- Duplicate waitlist protection (race-safe with FOR UPDATE on event row) ---
    norm_email = payload.participant_email.strip().lower()
    try:
        # `Event` is not imported at module level; without this local import the NameError was swallowed by
        # the broad except below and the row lock (which serializes the capacity/duplicate checks) never ran.
        from app.models.event_model import Event
        db.query(Event).filter(Event.id == event_id).with_for_update().first()
    except Exception:
        pass
    existing = (
        db.query(EventWaitlist)
        .filter(
            EventWaitlist.event_id == event_id,
            sa.func.lower(EventWaitlist.participant_email) == norm_email,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail="Already on the waitlist for this event.",
        )

    entry = EventWaitlist(
        event_id=event_id,
        participant_name=payload.participant_name,
        participant_email=norm_email,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def get_event_waitlist_service(db: Session, event_id: UUID):
    _get_event_or_404(db, event_id)
    from app.models.event_aux_models import EventWaitlist

    return db.query(EventWaitlist).filter(EventWaitlist.event_id == event_id).all()


def delete_waitlist_entry_service(
    db: Session,
    event_id: UUID,
    entry_id: UUID,
    current_user: dict | None = None,
    access_token: str | None = None,
):
    """Leave / remove a waitlist entry.

    - The person on the waitlist (case-insensitive email) may always remove their own entry.
    - Staff may remove any entry of an event they own (admin/provider of the owning tenant, or an
      active Platform Super Admin); staff of another tenant are refused.
    - Anyone else gets 404 (not 403) so another user's entry is not revealed.
    """
    from app.models.event_aux_models import EventWaitlist

    entry = (
        db.query(EventWaitlist)
        .filter(EventWaitlist.id == entry_id, EventWaitlist.event_id == event_id)
        .first()
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Waitlist entry not found")

    if current_user:
        user_email = (current_user.get("email") or "").strip().lower()
        entry_email = (entry.participant_email or "").strip().lower()
        if not (user_email and user_email == entry_email):
            if current_user.get("role") in (*STAFF_ROLES, "super_admin"):
                assert_event_access(db, _get_event_or_404(db, event_id), current_user, access_token=access_token)
            else:
                raise HTTPException(status_code=404, detail="Waitlist entry not found")

    entry.status = "left"
    db.commit()
    return {"message": "Removed from waitlist"}

def my_waitlist_service(db: Session, email: str, status: str | None = None):
    from app.models.event_aux_models import EventWaitlist
    import sqlalchemy as sa
    from sqlalchemy.orm import joinedload
    
    email_normalized = email.strip().lower()
    
    q = db.query(EventWaitlist).options(
        joinedload(EventWaitlist.event)
    ).filter(
        sa.func.lower(EventWaitlist.participant_email) == email_normalized
    )
    
    if status:
        q = q.filter(EventWaitlist.status == status)
        
    entries = q.order_by(EventWaitlist.created_at.desc()).all()
    
    results = []
    for w in entries:
        results.append({
            "id": w.id,
            "event_id": w.event_id,
            "event_title": w.event.title if w.event else None,
            "event_status": w.event.status if w.event else None,
            "event_start_date": w.event.start_date if w.event else None,
            "participant_name": w.participant_name,
            "participant_email": w.participant_email,
            "status": w.status,
            "registration_id": w.registration_id,
            "payment_offer_expires_at": w.payment_offer_expires_at,
            "created_at": w.created_at
        })
    return results


def _mask_session_meeting_links(sessions: list) -> list:
    """Same protection EventResponse/EventSessionResponse apply: never expose a real session URL."""
    masked = []
    for session in sessions or []:
        if isinstance(session, dict) and session.get("meeting_link"):
            session = {**session, "meeting_link": "protected"}
        masked.append(session)
    return masked


def _with_session_ids(sessions: list) -> list:
    """Give any legacy id-less session an id. Called only from paths that already rewrite the list."""
    import uuid

    return [
        {**session, "id": str(uuid.uuid4())} if isinstance(session, dict) and not session.get("id") else session
        for session in (sessions or [])
    ]


def get_sessions_service(db: Session, event_id: UUID, current_user: dict | None = None, access_token: str | None = None):
    """Agenda for an Event.

    Read-only: a GET never writes (legacy id-less sessions are given ids by the next session
    add/update/delete, or by an Event edit that resends the list). Staff of the owning tenant
    get the stored sessions; everyone else gets meeting_link == "protected" and must use the
    protected meeting-link endpoints for the real URL.
    """
    event = _get_event_or_404(db, event_id)
    viewer = build_event_viewer(db, current_user, access_token=access_token)
    if not can_view_event(db, event, viewer, access_token=access_token):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    sessions = list(event.sessions or [])
    if viewer_owns_event(event, viewer):
        return sessions
    return _mask_session_meeting_links(sessions)


def _validate_session_date(event, session_date):
    if session_date is None:
        return
    ev_start = event.start_date.date() if hasattr(event.start_date, "date") else event.start_date
    ev_end = event.end_date.date() if hasattr(event.end_date, "date") else event.end_date
    if not (ev_start <= session_date <= ev_end):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"session_date {session_date} must be within Event {ev_start}..{ev_end}",
        )


def _sort_sessions(sessions: list) -> list:
    def _key(s):
        sd = s.get("session_date") or ""
        # normalize date string for sorting
        st = s.get("start_time") or ""
        return (str(sd), str(st))

    return sorted(sessions, key=_key)


def add_session_service(db: Session, event_id: UUID, payload):
    import copy
    import uuid

    from sqlalchemy.orm.attributes import flag_modified

    event = _get_event_or_404(db, event_id)
    _validate_session_date(event, payload.session_date)
    sessions = _with_session_ids(copy.deepcopy(event.sessions or []))
    # model_dump will serialize date as date object; convert to ISO string for JSONB
    data = payload.model_dump()
    if data.get("session_date"):
        data["session_date"] = str(data["session_date"])
    new = {"id": str(uuid.uuid4()), **data}
    sessions.append(new)
    event.sessions = _sort_sessions(sessions)
    flag_modified(event, "sessions")
    db.commit()
    db.refresh(event)
    return new


def update_session_service(db: Session, event_id: UUID, session_id: str, payload):
    import copy

    from sqlalchemy.orm.attributes import flag_modified

    event = _get_event_or_404(db, event_id)
    sessions = _with_session_ids(copy.deepcopy(event.sessions or []))
    for s in sessions:
        if s.get("id") == session_id:
            updates = payload.model_dump(exclude_unset=True)
            if "session_date" in updates and updates["session_date"] is not None:
                _validate_session_date(event, updates["session_date"])
                updates["session_date"] = str(updates["session_date"])
            for k, v in updates.items():
                s[k] = v
            event.sessions = _sort_sessions(sessions)
            flag_modified(event, "sessions")
            db.commit()
            db.refresh(event)
            # return persisted version, not in-memory stale reference
            for updated in event.sessions:
                if updated.get("id") == session_id:
                    return updated
            return s
    raise HTTPException(status_code=404, detail="Session not found")


def delete_session_service(db: Session, event_id: UUID, session_id: str):
    from sqlalchemy.orm.attributes import flag_modified

    event = _get_event_or_404(db, event_id)
    original_len = len(event.sessions or [])
    sessions = [s for s in _with_session_ids(event.sessions or []) if s.get("id") != session_id]
    if len(sessions) == original_len:
        raise HTTPException(status_code=404, detail="Session not found")
    event.sessions = sessions
    flag_modified(event, "sessions")
    db.commit()
    db.refresh(event)
    return {"message": "Session deleted"}


def get_event_attendance_service(db: Session, event_id: UUID):
    from app.models.event_aux_models import EventRegistration
    from app.schemas.event_schema import EventAttendanceItem, EventAttendanceResponse

    event = _get_event_or_404(db, event_id)
    regs = db.query(EventRegistration).filter(EventRegistration.event_id == event_id).all()
    attended = [r for r in regs if r.status == "attended"]
    no_show = [r for r in regs if r.status == "no_show"]

    # Per-session breakdown from the dedicated session attendance records (one entry per session of the
    # event; None when the event has no sessions). EventRegistration.session_id is no longer the source.
    from app.services.event_session_attendance_service import attendance_by_session_view, summarize_session_attendance

    by_session = attendance_by_session_view(summarize_session_attendance(db, event))

    participants = [
        EventAttendanceItem(
            registration_id=r.id,
            participant_name=r.participant_name,
            participant_email=r.participant_email,
            status=r.status,
            checked_in_at=r.checked_in_at.isoformat() if r.checked_in_at else None,
            checked_in_by=r.checked_in_by,
            checked_out_at=r.checked_out_at.isoformat() if r.checked_out_at else None,
            session_id=r.session_id,
            ticket_type_id=r.ticket_type_id
        )
        for r in regs
    ]

    return EventAttendanceResponse(
        event_id=event_id,
        total_registered=len(regs),
        total_attended=len(attended),
        total_no_show=len(no_show),
        attendance_by_session=by_session,
        participants=participants
    )


def send_announcement_service(db: Session, event_id: UUID, payload, current_user: dict | None = None):
    from datetime import datetime
    from app.models.event_aux_models import EventRegistration

    event = _get_event_or_404(db, event_id)
    message = payload.message if hasattr(payload, "message") else payload.get("message", "") if isinstance(payload, dict) else str(payload)
    title = payload.title if hasattr(payload, "title") and payload.title else f"Announcement: {event.title}"

    regs = db.query(EventRegistration).filter(
        EventRegistration.event_id == event_id,
        EventRegistration.status.in_(["confirmed", "attended"])
    ).all()
    recipient_count = len(regs)

    # Dispatch notification to each registered participant — honor payload.channels if supplied (non-breaking fallback)
    from app.services.notification_triggers import _safe_notify
    # payload may be dict or Pydantic; respect requested channels, default to in_app+email for backward compat
    try:
        req_channels = payload.channels if hasattr(payload, "channels") and payload.channels else None
        if not req_channels and isinstance(payload, dict):
            req_channels = payload.get("channels")
    except Exception:
        req_channels = None
    effective_channels = req_channels if req_channels else ["in_app", "email"]
    sent_count = 0
    for reg in regs:
        try:
            _safe_notify(
                db, title, message, "event_announcement", event.tenant_id,
                {"event_id": str(event_id), "registration_id": str(reg.id)},
                participant_email=reg.participant_email,
                channels=effective_channels,
            )
            sent_count += 1
        except Exception as e:
            logger.warning("Announcement dispatch failed for %s: %s", reg.participant_email, e)

    return {"id": str(event_id), "event_id": str(event_id), "sent_by": current_user.get("id") if current_user else None,
            "recipient_count": recipient_count, "sent_count": sent_count, "created_at": datetime.utcnow().isoformat(), "title": title, "message": message}

def create_feedback_service(db: Session, event_id: UUID, payload: dict, is_review: bool = False):
    _get_event_or_404(db, event_id)
    from app.models.event_aux_models import EventFeedback, EventRegistration
    # Verified review: only registered participants (confirmed/attended) can submit ratings/reviews — email required
    if is_review:
        email = payload.get("participant_email")
        if not email:
            raise HTTPException(status_code=400, detail="participant_email is required for verified reviews")
        reg = db.query(EventRegistration).filter(EventRegistration.event_id==event_id, EventRegistration.participant_email==email, EventRegistration.status.in_(["confirmed","attended"])).first()
        if not reg:
            raise HTTPException(status_code=403, detail="Only registered participants can submit verified reviews")
    fb = EventFeedback(
        event_id=event_id,
        participant_email=payload.get("participant_email"),
        form_id=payload.get("form_id"),
        answers=payload.get("answers"),
        rating=payload.get("rating"),
        comment=payload.get("comment"),
        is_review=is_review,
    )
    db.add(fb)
    db.commit()
    db.refresh(fb)
    return fb


def get_event_feedbacks_service(db: Session, event_id: UUID, is_review: bool = False):
    _get_event_or_404(db, event_id)
    from app.models.event_aux_models import EventFeedback

    return db.query(EventFeedback).filter(EventFeedback.event_id == event_id, EventFeedback.is_review == is_review).all()


def moderate_review_service(db: Session, review_id: UUID, action: str, event_id: UUID | None = None):
    from app.models.event_aux_models import EventFeedback
    allowed = {"approved", "rejected", "pending"}
    if action not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid moderation action. Allowed: {sorted(allowed)}")
    conditions = [EventFeedback.id == review_id]
    if event_id is not None:
        # The route already proved ownership of ``event_id``; the review must belong to that event.
        conditions.append(EventFeedback.event_id == event_id)
    fb = db.query(EventFeedback).filter(*conditions).first()
    if not fb:
        raise HTTPException(status_code=404, detail="Review not found")
    fb.moderation_status = action
    db.commit()
    return fb


def get_event_reports_service(db: Session, event_id: UUID, report_type: str):
    from app.models.event_aux_models import EventFeedback, EventRegistration

    event = _get_event_or_404(db, event_id)
    regs = db.query(EventRegistration).filter(EventRegistration.event_id == event_id).all()

    if report_type == "registration":
        by_status: dict = {}
        by_ticket: dict = {}
        for r in regs:
            by_status[r.status] = by_status.get(r.status, 0) + 1
            if r.ticket_type_id:
                by_ticket[r.ticket_type_id] = by_ticket.get(r.ticket_type_id, 0) + 1
        data = {"total_registrations": len(regs), "by_status": by_status, "by_ticket_type": by_ticket}
    elif report_type == "attendance":
        attended = sum(1 for r in regs if r.status == "attended")
        no_show = sum(1 for r in regs if r.status == "no_show")
        cancelled = sum(1 for r in regs if r.status == "cancelled")
        from app.services.event_session_attendance_service import attendance_by_session_view, summarize_session_attendance

        by_session = attendance_by_session_view(summarize_session_attendance(db, event)) or {}
        data = {"total": len(regs), "attended": attended, "no_show": no_show, "cancelled": cancelled, "by_session": by_session}
    elif report_type == "feedback":
        feedbacks = db.query(EventFeedback).filter(EventFeedback.event_id == event_id, EventFeedback.is_review.is_(False)).all()
        reviews = db.query(EventFeedback).filter(EventFeedback.event_id == event_id, EventFeedback.is_review.is_(True)).all()
        ratings = [int(r.rating) for r in reviews if r.rating and str(r.rating).isdigit()]
        avg_rating = sum(ratings)/len(ratings) if ratings else None
        data = {"total_feedbacks": len(feedbacks), "total_reviews": len(reviews), "average_rating": avg_rating,
                "feedbacks": [{"id": str(f.id), "rating": f.rating, "comment": f.comment} for f in feedbacks[:20]]}
    elif report_type == "revenue":
        # Revenue is what was actually paid (EventOrder), not ticket prices x registrations: that counted
        # unpaid/free registrations and ignored quantity and refunds. Same shape as before, sourced from orders.
        from app.services.event_dashboard_service import compute_revenue_report

        data = compute_revenue_report(db, event_id, event.currency)
    elif report_type == "cancellation":
        cancelled = [r for r in regs if r.status == "cancelled"]
        # also orders refund_requested
        try:
            from app.models.event_aux_models import EventOrder
            refunds = db.query(EventOrder).filter(EventOrder.event_id==event_id, EventOrder.status.in_(["refund_requested","refunded"])).all()
            by_reason = {}
            for o in refunds:
                by_reason[o.refund_reason or "unknown"] = by_reason.get(o.refund_reason or "unknown", 0) + 1
            data = {"total_cancelled": len(cancelled), "total_refunds": len(refunds), "by_reason": by_reason, "cancelled": [{"email": r.participant_email, "qr": r.qr_code} for r in cancelled[:20]]}
        except Exception:
            data = {"total_cancelled": len(cancelled), "cancelled": [{"email": r.participant_email} for r in cancelled[:20]]}
    elif report_type == "completion":
        total = len(regs)
        attended = sum(1 for r in regs if r.status=="attended")
        completion_rate = round(attended/total*100 if total else 0,2)
        data = {"total": total, "completed": attended, "completion_rate": completion_rate, "event_status": event.status, "is_completed": event.status=="completed"}
    else:
        by_status = {}
        for r in regs:
            by_status[r.status] = by_status.get(r.status, 0) + 1
        data = {"total_registrations": len(regs), "by_status": by_status}

    return {"event_id": str(event_id), "type": report_type, "data": data}


def get_event_summary_service(
    db: Session,
    enterprise_id: UUID | None = None,
    *,
    tenant_id: UUID | str | None = None,
    platform_wide: bool = False,
):
    """Aggregate dashboard for a tenant.

    Scope comes from the authenticated caller (``tenant_id``) — never from a request
    parameter. ``platform_wide=True`` (explicit, internal, Platform Super Admin only) is the
    only way to aggregate across tenants; omitting both is refused rather than defaulting to "all".
    """
    from sqlalchemy import func
    from app.models.event_aux_models import EventFeedback, EventRegistration
    from app.models.event_model import Event

    q = db.query(Event).filter(Event.is_deleted.is_(False))
    if tenant_id is not None:
        owned = event_tenant_clause(tenant_id)
        if owned is None:
            raise HTTPException(status_code=403, detail="Tenant could not be resolved for this caller")
        q = q.filter(owned)
    elif not platform_wide:
        raise HTTPException(status_code=403, detail="Tenant could not be resolved for this caller")
    if enterprise_id:
        # Narrowing only: combined with the tenant clause above, another tenant's enterprise yields nothing.
        q = q.filter(Event.enterprise_id == enterprise_id)

    # by_status
    status_rows = q.with_entities(Event.status, func.count(Event.id)).group_by(Event.status).all()
    by_status = {row[0]: row[1] for row in status_rows}
    total = sum(by_status.values())

    # by_category
    cat_rows = q.with_entities(Event.category, func.count(Event.id)).group_by(Event.category).all()
    by_category = {row[0]: row[1] for row in cat_rows if row[0]}

    # by_delivery_mode
    del_rows = q.with_entities(Event.delivery_mode, func.count(Event.id)).group_by(Event.delivery_mode).all()
    by_delivery_mode = {row[0]: row[1] for row in del_rows if row[0]}

    from datetime import datetime
    now = datetime.utcnow()
    upcoming = q.filter(Event.start_date > now).count()
    past = q.filter(Event.start_date <= now).count()

    event_ids = [e.id for e in q.all()]
    reg_count = 0
    attended_count = 0
    avg_rating = None
    if event_ids:
        reg_count = db.query(func.count(EventRegistration.id)).filter(EventRegistration.event_id.in_(event_ids)).scalar() or 0
        attended_count = db.query(func.count(EventRegistration.id)).filter(EventRegistration.event_id.in_(event_ids), EventRegistration.status == "attended").scalar() or 0
        # rating is String, cast attempt
        try:
            avg_rating = db.query(func.avg(EventFeedback.rating.cast(sa.Float))).filter(EventFeedback.event_id.in_(event_ids), EventFeedback.is_review.is_(True)).scalar()
            if avg_rating is not None:
                avg_rating = float(avg_rating)
        except Exception:
            pass

    return {"total_events": total, "by_status": by_status, "by_category": by_category, "by_delivery_mode": by_delivery_mode,
            "upcoming_events": upcoming, "past_events": past, "total_registrations": reg_count, "total_attended": attended_count, "average_rating": avg_rating}


def _resolve_auth_tenant_id(current_user: dict | None) -> str | None:
    if not current_user:
        return None
    return (
        current_user.get("tenant_id")
        or current_user.get("tenantId")
        or current_user.get("tenant_id_claim")
        or current_user.get("org_id")
        or current_user.get("organization_id")
    )


def _validate_payload_tenant_id(payload_tenant_id, auth_tenant_id):
    """Validate supplied tenant_id belongs to authenticated user. Raise 403 if mismatch."""
    if payload_tenant_id and auth_tenant_id and str(payload_tenant_id) != str(auth_tenant_id):
        raise HTTPException(status_code=403, detail="Supplied tenant_id does not belong to authenticated user")


def _template_owner_tenant(db: Session, tmpl) -> str | None:
    """Owning tenant of a template: its own tenant_id, else its Enterprise's tenant (legacy rows)."""
    if tmpl.tenant_id:
        return str(tmpl.tenant_id).strip().lower()
    if tmpl.enterprise_id:
        ent = db.query(Enterprise).filter(Enterprise.id == tmpl.enterprise_id).first()
        if ent is not None and ent.tenant_id:
            return str(ent.tenant_id).strip().lower()
    return None


def _assert_template_access(db: Session, tmpl, current_user: dict | None, access_token: str | None = None) -> str | None:
    """Fail-closed tenant isolation for templates; returns the caller's tenant (None for the platform).

    The caller's tenant is resolved from the token, then the database / identity service — a
    missing tenant claim is a denial, not "access to everything". A template with no
    resolvable owner is reachable only by an active Platform Super Admin.
    """
    if is_platform_super_admin(current_user):
        return None
    caller_tenant = resolve_caller_tenant_id(db, current_user, access_token=access_token)
    owner_tenant = _template_owner_tenant(db, tmpl)
    if not caller_tenant or not owner_tenant or caller_tenant != owner_tenant:
        raise HTTPException(status_code=403, detail="Template does not belong to your tenant")
    return caller_tenant


def _verify_enterprise_in_tenant(db: Session, enterprise_id, tenant_id) -> None:
    """403 when the Enterprise belongs to a different tenant than the caller's."""
    try:
        enterprise_uuid = UUID(str(enterprise_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid enterprise_id")
    ent = db.query(Enterprise).filter(Enterprise.id == enterprise_uuid).first()
    if ent is None:
        raise HTTPException(status_code=404, detail="Enterprise not found")
    if ent.tenant_id and str(ent.tenant_id).strip().lower() != str(tenant_id).strip().lower():
        raise HTTPException(status_code=403, detail="enterprise_id does not belong to your tenant")


def create_template_service(db: Session, payload: dict, current_user: dict | None = None, access_token: str | None = None):
    from app.models.event_aux_models import EventTemplate

    # The template is always created under the caller's own tenant. A tenant_id in the body is
    # only accepted when it equals it (never trusted on its own, even when the token lacks the claim).
    auth_tenant_id = resolve_caller_tenant_id(db, current_user, access_token=access_token)
    if not auth_tenant_id:
        raise HTTPException(status_code=403, detail="Tenant could not be resolved for this caller")
    _validate_payload_tenant_id(payload.get("tenant_id"), auth_tenant_id)
    tenant_id = auth_tenant_id

    enterprise_id = payload.get("enterprise_id")
    if enterprise_id:
        _verify_enterprise_in_tenant(db, enterprise_id, tenant_id)
    elif current_user:
        enterprise_id = current_user.get("enterprise_id") or current_user.get("enterpriseId")
        if not enterprise_id and current_user.get("tenant_slug"):
            try:
                ent = db.query(Enterprise).filter(Enterprise.slug == current_user.get("tenant_slug")).first()
                if ent:
                    enterprise_id = str(ent.id)
            except Exception:
                pass
        if enterprise_id:
            try:
                _verify_enterprise_in_tenant(db, enterprise_id, tenant_id)
            except HTTPException:
                enterprise_id = None  # a default that is not the caller's must not be attached

    from app.services.event_template_mapping import template_provenance
    provenance = template_provenance(db, payload)
    tmpl = EventTemplate(
        **provenance,
        name=payload.get("name", "Template"),
        template_data=payload.get("template_data", payload),
        tenant_id=UUID(str(tenant_id)),
        enterprise_id=UUID(str(enterprise_id)) if enterprise_id else None,
    )
    db.add(tmpl)
    db.commit()
    db.refresh(tmpl)
    return tmpl

# ---- Free/Paid: checkout / orders / refund. ----

def _resolve_ticket(event, ticket_type_id: str | None):
    if not ticket_type_id:
        # free event or single price
        return {"price": event.price or "0", "currency": event.currency or "INR", "capacity": event.capacity}
    tickets = event.ticket_types or []
    for t in tickets:
        if isinstance(t, dict) and str(t.get("id")) == str(ticket_type_id):
            return t
    return None

def _ticket_effective_price(ticket: dict, event) -> str:
    from datetime import datetime
    now = datetime.utcnow()
    # early-bird
    eb_price = ticket.get("early_bird_price")
    eb_until = ticket.get("early_bird_until")
    if eb_price and eb_until:
        try:
            # eb_until may be string
            from datetime import datetime as _dt
            dt = _dt.fromisoformat(str(eb_until).replace("Z", ""))
            if now <= dt:
                return str(eb_price)
        except Exception:
            pass
    if ticket.get("promo_price"):
        return str(ticket["promo_price"])
    return str(ticket.get("price", event.price or "0"))

def create_event_checkout_service(db: Session, event_id: UUID, payload):
    import uuid
    from app.models.event_aux_models import EventOrder, EventRegistration, EventWaitlist
    from app.models.event_model import Event
    import sqlalchemy as sa
    from sqlalchemy.exc import IntegrityError
    from datetime import datetime
    from decimal import Decimal

    # Normalize email as established in Phase A
    participant_email = payload.participant_email.strip().lower()

    event = _get_event_or_404(db, event_id)
    if event.status in ["cancelled", "completed", "archived", "suspended"]:
        raise HTTPException(status_code=400, detail=f"Checkout closed — event is {event.status}")
    if event.status not in ["published"]:
        raise HTTPException(status_code=400, detail=f"Event not open for checkout (status: {event.status})")
    
    # Registration window enforcement (same as free registration)
    from app.utils.event_utils import validate_registration_window
    validate_registration_window(event)
    
    ticket = _resolve_ticket(event, payload.ticket_type_id)
    if payload.ticket_type_id and not ticket:
        raise HTTPException(status_code=404, detail="Ticket type not found")

    # Phase 2.8: validate + price meal/accommodation selections BEFORE taking the Event row lock — pure
    # validation (unknown/retired/duplicate/window/currency) never needs the lock, so bad input fails fast.
    from app.services.event_option_pricing_service import (
        assert_capacity_available, persist_line_items, resolve_priced_selections,
    )
    ticket_currency_for_pricing = (
        ticket.get("currency", event.currency) if isinstance(ticket, dict) else (event.currency or "INR")
    ) or "INR"
    priced_options = resolve_priced_selections(
        event,
        meal_selections=getattr(payload, "meal_selections", None),
        accommodation_selections=getattr(payload, "accommodation_selections", None),
        ticket_currency=ticket_currency_for_pricing,
    )

    try:
        # capacity per ticket type — lock Event row to prevent race
        db.query(Event).filter(Event.id == event_id).with_for_update().first()
        
        # Duplicate registration protection (Phase A rule)
        existing_reg = db.query(EventRegistration).filter(
            EventRegistration.event_id == event_id,
            sa.func.lower(EventRegistration.participant_email) == participant_email,
            EventRegistration.status.in_(["confirmed", "attended"])
        ).first()
        
        if existing_reg:
            raise HTTPException(status_code=409, detail="Participant is already registered for this event.")

        if ticket and ticket.get("capacity"):
            try:
                cap = int(float(str(ticket["capacity"]).strip()))
                # One purchase creates one order AND one registration — count each seat once.
                cnt = _seats_taken(db, event_id, payload.ticket_type_id)
                if cnt + payload.quantity > cap:
                    raise HTTPException(status_code=400, detail=f"Ticket type at capacity ({cap})")
            except ValueError:
                pass

        # Phase 2.8: meal/accommodation capacity — same row lock as ticket capacity above, so this is
        # serialized against concurrent purchases of the same option exactly like tickets already are.
        assert_capacity_available(db, event, priced_options)

        # Waitlist offer processing
        waitlist_entry = None
        waitlist_id = getattr(payload, "waitlist_id", None)
        if waitlist_id:
            # Scoped to THIS event: an offer for another event must never unlock a seat here.
            waitlist_entry = db.query(EventWaitlist).filter(
                EventWaitlist.id == waitlist_id,
                EventWaitlist.event_id == event_id,
            ).with_for_update().first()
            if not waitlist_entry:
                raise HTTPException(status_code=404, detail="Waitlist entry not found")
            if waitlist_entry.participant_email.strip().lower() != participant_email:
                raise HTTPException(status_code=403, detail="Waitlist offer belongs to another user")
            if waitlist_entry.status != "payment_pending":
                raise HTTPException(status_code=400, detail="Waitlist offer is not valid for payment")
            if waitlist_entry.payment_offer_expires_at and waitlist_entry.payment_offer_expires_at < datetime.utcnow():
                raise HTTPException(status_code=400, detail="Waitlist payment offer has expired")
        else:
            # Enforce overall event capacity for normal public checkout
            if event.capacity:
                try:
                    max_capacity = int(float(str(event.capacity).strip()))
                    current_count = _seats_taken(db, event_id)
                    if current_count + payload.quantity > max_capacity:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Event is at full capacity ({max_capacity} participants). Please join waitlist."
                        )
                except ValueError:
                    pass
        price = _ticket_effective_price(ticket or {}, event)
        try:
            ticket_subtotal_decimal = (Decimal(price) * payload.quantity).quantize(Decimal("0.01"))
        except Exception:
            # Same fallback the pre-Phase-2.8 code used for an unparseable price: treat it as free rather than
            # blocking checkout on a malformed ticket price (a pre-existing data-quality concern, not new here).
            ticket_subtotal_decimal = Decimal("0.00")

        currency = ticket.get("currency", event.currency) if isinstance(ticket, dict) else (event.currency or "INR")

        # Phase 2.8: grand total = ticket + meal + accommodation. A free ticket (ticket_subtotal 0) with paid
        # extras still produces a payable order — "free event" is a ticket-price concept, not a total-due one.
        # Computed directly in Decimal (compute_totals' float-for-JSON output is for API responses, e.g. the
        # quote endpoint — not for this persisted amount string).
        grand_total_decimal = (
            ticket_subtotal_decimal + priced_options.meal_subtotal + priced_options.accommodation_subtotal
        ).quantize(Decimal("0.01"))
        amount = str(grand_total_decimal)

        payment_status = "confirmed"  # stub: payment always confirmed (marketplace/merchant)
        order = EventOrder(
            event_id=event_id,
            participant_name=payload.participant_name,
            participant_email=participant_email, # Normalized email
            ticket_type_id=payload.ticket_type_id,
            quantity=str(payload.quantity),
            amount=amount,
            currency=currency,
            payment_status=payment_status,
            status="confirmed",
            payment_provider=payload.payment_provider or "marketplace",
            ticket_subtotal=str(ticket_subtotal_decimal),
            meal_subtotal=str(priced_options.meal_subtotal),
            accommodation_subtotal=str(priced_options.accommodation_subtotal),
        )
        db.add(order)
        db.flush() # Secure order details without committing

        # also create registration for attendance tracking (Atomic with order)
        reg = EventRegistration(
            event_id=event_id,
            participant_name=payload.participant_name,
            participant_email=participant_email, # Normalized email
            ticket_type_id=payload.ticket_type_id,
            status="confirmed",
            qr_code=str(uuid.uuid4())[:12].upper(),
            meal_selections=[line.option_id for line in priced_options.meal_lines] or None,
            accommodation_selections=[line.option_id for line in priced_options.accommodation_lines] or None,
        )
        db.add(reg)
        db.flush()

        # Phase 2.8: one immutable snapshot row per purchased meal/accommodation option.
        persist_line_items(db, event_id=event_id, registration_id=reg.id, order_id=order.id, priced=priced_options)

        # If checked out via waitlist offer, fulfill it
        if waitlist_entry:
            waitlist_entry.status = "promoted"
            waitlist_entry.registration_id = reg.id

        # Commit BOTH order and registration (and waitlist update) atomically
        db.commit()
        db.refresh(order)
        
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as e:
        db.rollback()
        logger.error(f"IntegrityError during checkout: {e}")
        # Triggers if race condition bypasses select for unique index
        raise HTTPException(status_code=409, detail="Participant is already registered for this event.")
    except Exception as e:
        db.rollback()
        logger.error(f"Unexpected error during checkout: {e}")
        raise HTTPException(status_code=500, detail="An error occurred during checkout processing.")

    try:
        from app.services.notification_triggers import notify_payment_success
        notify_payment_success(db, event, order)
    except Exception as e:
        logger.warning(f"Failed to send checkout notification: {e}")

    return order


def get_checkout_quote_service(db: Session, event_id: UUID, payload) -> dict:
    """The authoritative price breakdown checkout would charge for this ticket + meal/accommodation selection.
    A pure preview: no registration, order, or line item is created, and no row lock is taken — this is
    advisory, not a reservation. The SAME capacity/pricing rules are re-applied, authoritatively and under
    lock, at actual checkout (create_event_checkout_service); a quote can go stale between the two calls, same
    as any other price preview. Reuses the SAME resolve_priced_selections/assert_capacity_available/
    compute_totals functions checkout itself uses — see event_option_pricing_service.
    """
    from decimal import Decimal
    from app.services.event_option_pricing_service import assert_capacity_available, compute_totals, resolve_priced_selections

    event = _get_event_or_404(db, event_id)
    if event.status in ["cancelled", "completed", "archived", "suspended"]:
        raise HTTPException(status_code=400, detail=f"Checkout closed — event is {event.status}")
    if event.status not in ["published"]:
        raise HTTPException(status_code=400, detail=f"Event not open for checkout (status: {event.status})")

    from app.utils.event_utils import validate_registration_window
    validate_registration_window(event)

    ticket = _resolve_ticket(event, payload.ticket_type_id)
    if payload.ticket_type_id and not ticket:
        raise HTTPException(status_code=404, detail="Ticket type not found")

    ticket_currency = (
        ticket.get("currency", event.currency) if isinstance(ticket, dict) else (event.currency or "INR")
    ) or "INR"
    priced_options = resolve_priced_selections(
        event, meal_selections=payload.meal_selections, accommodation_selections=payload.accommodation_selections,
        ticket_currency=ticket_currency,
    )
    assert_capacity_available(db, event, priced_options)  # advisory (no lock) — see docstring

    price = _ticket_effective_price(ticket or {}, event)
    try:
        ticket_subtotal = (Decimal(price) * payload.quantity).quantize(Decimal("0.01"))
    except Exception:
        # Same fallback create_event_checkout_service uses for an unparseable ticket price.
        ticket_subtotal = Decimal("0.00")

    return compute_totals(ticket_subtotal, priced_options, currency=priced_options.currency or ticket_currency)


def get_event_orders_service(db: Session, event_id: UUID):
    from app.models.event_aux_models import EventOrder
    _get_event_or_404(db, event_id)
    return db.query(EventOrder).filter(EventOrder.event_id==event_id).order_by(EventOrder.created_at.desc()).all()

def create_event_refund_service(
    db: Session,
    event_id: UUID,
    reg_id: UUID,
    payload,
    current_user: dict | None = None,
    access_token: str | None = None,
    staff_verified: bool = False,
):
    """Request a refund for an order (paid) or cancel a registration (free).

    Authorization: the participant themself (case-insensitive email) or staff who own the Event.
    ``staff_verified`` is set only by routes whose dependency already proved ownership.
    """
    from app.models.event_aux_models import EventOrder, EventRegistration
    event = _get_event_or_404(db, event_id)
    actor = _actor_id(current_user)
    reason = payload.reason if payload and getattr(payload, "reason", None) else None
    # Try order first, then registration
    order = db.query(EventOrder).filter(EventOrder.event_id==event_id, EventOrder.id==reg_id).first()
    if order:
        _authorize_participant_or_staff(
            db, event, order.participant_email, current_user,
            access_token=access_token, staff_verified=staff_verified,
            forbidden_detail="Not authorized to refund this order",
        )
        if order.status in ("refunded",):
            raise HTTPException(status_code=400, detail="Already refunded")
        if order.payment_status == "refunded":
            raise HTTPException(status_code=400, detail="Already refunded")
        # if attended, no refund per spec
        # check registration attended
        reg = db.query(EventRegistration).filter(EventRegistration.event_id==event_id, EventRegistration.participant_email==order.participant_email, EventRegistration.status=="attended").first()
        if reg:
            raise HTTPException(status_code=400, detail="Cannot refund after attendance (checked-in)")
        previous = order.status
        order.status = "refund_requested"
        order.payment_status = "refund_requested"
        order.refund_reason = reason
        _log_audit(
            db, event_id, "refund",
            {"order_id": str(order.id), "status": previous},
            {"order_id": str(order.id), "status": "refund_requested"},
            changed_by=actor, notes=reason, commit=False,
        )
        db.commit(); db.refresh(order)
        try:
            from app.services.notification_triggers import notify_refund_status
            notify_refund_status(db, event, order, "refund_requested")
        except Exception:
            pass
        return order
    # fallback: registration refund/cancel
    reg = db.query(EventRegistration).filter(EventRegistration.id==reg_id, EventRegistration.event_id==event_id).first()
    if not reg:
        raise HTTPException(status_code=404, detail="Registration/Order not found")
    _authorize_participant_or_staff(
        db, event, reg.participant_email, current_user,
        access_token=access_token, staff_verified=staff_verified,
        forbidden_detail="Not authorized to refund this registration",
    )
    if reg.status == "attended":
        raise HTTPException(status_code=400, detail="Cannot refund after attendance")
    if reg.status != "cancelled":
        # A paid registration's order must not be left looking settled: record the refund request on it.
        paid_order = _linked_order_for_registration(db, reg)
        if paid_order is not None and paid_order.status == "confirmed":
            paid_order.status = "refund_requested"
            paid_order.payment_status = "refund_requested"
            paid_order.refund_reason = reason
        _cancel_registration_and_release_seat(db, event, reg, actor=actor, audit_action="refund", notes=reason)
    db.commit()
    try:
        from app.services.notification_triggers import notify_refund_status
        notify_refund_status(db, event, reg, "refund_requested")
    except Exception:
        pass
    return {"message": "Refund requested", "registration_id": str(reg.id), "status": reg.status}


# ---- EventCategory CRUD ----


def create_event_category_service(db: Session, payload):
    from app.models.event_aux_models import EventCategory

    name = payload.name.strip()
    existing = db.query(EventCategory).filter(EventCategory.name == name).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Category '{name}' already exists")
    if payload.parent_id:
        parent = db.query(EventCategory).filter(EventCategory.id == payload.parent_id).first()
        if not parent:
            raise HTTPException(status_code=404, detail="Parent category not found")
    cat = EventCategory(name=name, parent_id=payload.parent_id, description=payload.description)
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


def list_event_categories_service(db: Session):
    from app.models.event_aux_models import EventCategory
    return db.query(EventCategory).order_by(EventCategory.name).all()


def update_event_category_service(db: Session, category_id: UUID, payload):
    from app.models.event_aux_models import EventCategory

    cat = db.query(EventCategory).filter(EventCategory.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    if payload.name is not None:
        new_name = payload.name.strip()
        dup = db.query(EventCategory).filter(EventCategory.name == new_name, EventCategory.id != category_id).first()
        if dup:
            raise HTTPException(status_code=400, detail=f"Category '{new_name}' already exists")
        cat.name = new_name
    if payload.description is not None:
        cat.description = payload.description
    db.commit()
    db.refresh(cat)
    return cat


def delete_event_category_service(db: Session, category_id: UUID):
    from app.models.event_aux_models import EventCategory

    cat = db.query(EventCategory).filter(EventCategory.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    # Check if any subcategories exist
    child_count = db.query(EventCategory).filter(EventCategory.parent_id == category_id).count()
    if child_count > 0:
        raise HTTPException(status_code=400, detail=f"Cannot delete: category has {child_count} subcategories. Delete subcategories first.")
    db.delete(cat)
    db.commit()
    return {"message": "Category deleted"}


# ---- Template CRUD (add update/delete) ----


def get_template_service(db: Session, template_id: UUID, current_user: dict | None = None, access_token: str | None = None):
    from app.models.event_aux_models import EventTemplate
    tmpl = db.query(EventTemplate).filter(EventTemplate.id == template_id).first()
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found")
    _assert_template_access(db, tmpl, current_user, access_token)
    return tmpl


def update_template_service(db: Session, template_id: UUID, payload: dict, current_user: dict | None = None, access_token: str | None = None):
    from app.models.event_aux_models import EventTemplate
    tmpl = db.query(EventTemplate).filter(EventTemplate.id == template_id).first()
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found")
    _assert_template_access(db, tmpl, current_user, access_token)
    from app.services.event_template_mapping import template_provenance
    provenance = template_provenance(db, payload, tmpl)
    for key, value in provenance.items():
        setattr(tmpl, key, value)
    if "name" in payload:
        tmpl.name = payload["name"]
    if "template_data" in payload:
        tmpl.template_data = payload["template_data"]
    db.commit()
    db.refresh(tmpl)
    return tmpl


def delete_template_service(db: Session, template_id: UUID, current_user: dict | None = None, access_token: str | None = None):
    from app.models.event_aux_models import EventTemplate
    tmpl = db.query(EventTemplate).filter(EventTemplate.id == template_id).first()
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found")
    _assert_template_access(db, tmpl, current_user, access_token)
    db.delete(tmpl)
    db.commit()
    return {"message": "Template deleted"}


# ---- Batch Check-in ----


def _actor_uuid(current_user: dict | None):
    """The acting user's id as a UUID for ``checked_in_by`` (None when absent or not a UUID)."""
    from uuid import UUID as _UUID

    if not current_user or not current_user.get("id"):
        return None
    try:
        return _UUID(str(current_user["id"]))
    except (ValueError, AttributeError):
        return None


def _validate_session_for_event(event, session_id: str | None) -> None:
    """A session_id supplied to check-in must be one of the Event's own embedded sessions."""
    if not session_id or event is None:
        return
    known = {str(item.get("id")) for item in (event.sessions or []) if isinstance(item, dict) and item.get("id")}
    if str(session_id) not in known:
        raise HTTPException(status_code=400, detail="session_id does not belong to this event")


def _registration_is_refunded(db: Session, reg) -> bool:
    """True when the order that paid for ``reg`` has been refunded (covers rows refunded before
    approval started cancelling the registration)."""
    order = _linked_order_for_registration(db, reg)
    return order is not None and (order.status == "refunded" or order.payment_status == "refunded")


def batch_checkin_service(db: Session, event_id: UUID, participants: list, current_user: dict | None = None):
    from datetime import datetime
    from app.models.event_aux_models import EventRegistration
    from app.models.event_model import Event as Ev

    _get_event_or_404(db, event_id)
    ev_chk = db.query(Ev).filter(Ev.id == event_id).first()
    if ev_chk and ev_chk.status in ["cancelled", "completed", "archived", "suspended"]:
        raise HTTPException(status_code=400, detail=f"Cannot check-in: event is {ev_chk.status}")

    actor_uuid = _actor_uuid(current_user)
    actor = _actor_id(current_user)
    results = []
    succeeded = 0
    failed = 0
    for item in participants:
        reg = None
        if item.registration_id:
            reg = db.query(EventRegistration).filter(
                EventRegistration.id == item.registration_id,
                EventRegistration.event_id == event_id
            ).first()
        elif item.qr_code:
            reg = db.query(EventRegistration).filter(
                EventRegistration.qr_code == item.qr_code,
                EventRegistration.event_id == event_id
            ).first()
        if not reg:
            results.append({
                "registration_id": item.registration_id or item.qr_code,
                "status": "failed",
                "message": "Registration not found"
            })
            failed += 1
            continue
        try:
            _validate_session_for_event(ev_chk, item.session_id)
        except HTTPException as exc:
            results.append({
                "registration_id": reg.id,
                "participant_name": reg.participant_name,
                "participant_email": reg.participant_email,
                "status": "failed",
                "message": exc.detail,
            })
            failed += 1
            continue
        if reg.status == "cancelled":
            results.append({
                "registration_id": reg.id,
                "participant_name": reg.participant_name,
                "participant_email": reg.participant_email,
                "status": "failed",
                "message": "Registration is cancelled"
            })
            failed += 1
            continue
        if reg.status == "attended":
            results.append({
                "registration_id": reg.id,
                "participant_name": reg.participant_name,
                "participant_email": reg.participant_email,
                "status": "already_checked_in",
                "checked_in_at": reg.checked_in_at.isoformat() if reg.checked_in_at else None,
                "message": "Already checked in"
            })
            succeeded += 1
            continue
        if _registration_is_refunded(db, reg):
            results.append({
                "registration_id": reg.id,
                "participant_name": reg.participant_name,
                "participant_email": reg.participant_email,
                "status": "failed",
                "message": "Registration was refunded"
            })
            failed += 1
            continue
        reg.status = "attended"
        reg.checked_in_at = datetime.utcnow()
        reg.checked_in_by = actor_uuid  # the operator who ran the batch
        if item.session_id:
            reg.session_id = item.session_id
        _log_audit(
            db, event_id, "batch_check_in", None,
            {"registration_id": str(reg.id), "participant_email": reg.participant_email, "session_id": item.session_id},
            changed_by=actor, commit=False,
        )
        results.append({
            "registration_id": reg.id,
            "participant_name": reg.participant_name,
            "participant_email": reg.participant_email,
            "status": "attended",
            "checked_in_at": reg.checked_in_at.isoformat(),
            "message": "Checked in"
        })
        succeeded += 1
    db.commit()
    return {"total": len(participants), "succeeded": succeeded, "failed": failed, "results": results}


# ---- Waitlist Auto-Promote ----


def cancel_registration_service(db: Session, event_id: UUID, reg_id: UUID, current_user: dict, access_token: str | None = None):
    """Cancel a registration: by the participant themself or by staff who own the Event."""
    from app.models.event_aux_models import EventRegistration

    reg = db.query(EventRegistration).filter(EventRegistration.id == reg_id, EventRegistration.event_id == event_id).first()
    if not reg:
        raise HTTPException(status_code=404, detail="Registration not found")
    event = get_event_by_id(db, event_id)
    _authorize_participant_or_staff(
        db, event, reg.participant_email, current_user,
        access_token=access_token, forbidden_detail="Not authorized to cancel this registration",
    )
    if reg.status == "attended":
        raise HTTPException(status_code=400, detail="Cannot cancel: already attended (use refund flow)")
    if reg.status == "cancelled":
        return {"message": "Registration already cancelled"}
    if event is not None:
        _cancel_registration_and_release_seat(db, event, reg, actor=_actor_id(current_user), audit_action="registration_cancel")
    else:  # orphaned registration of a removed event: nothing to promote into
        reg.status = "cancelled"
    db.commit()
    if event is not None:
        try:
            from app.services.notification_triggers import notify_single_cancellation
            notify_single_cancellation(db, event, reg)
        except Exception:
            logger.warning("Cancellation notification failed for registration %s", reg_id, exc_info=True)
    return {"message": "Registration cancelled"}


def _try_promote_from_waitlist(db: Session, event_id: UUID, event=None):
    """If a seat has opened, promote the oldest waiting entry (FIFO).

    Free event -> a confirmed registration is created. Paid event (top-level price OR ticket types)
    -> the entry becomes ``payment_pending`` with a 15-minute offer redeemed through checkout
    (``waitlist_id``); ``expire_waitlist_offer_task`` releases it and offers the next person.
    Only flushes — the caller owns the transaction. Seats already promised to a live payment offer
    count as taken, so repeated triggers can never offer more seats than exist.
    """
    import uuid
    from datetime import datetime, timedelta
    from app.models.event_aux_models import EventRegistration, EventWaitlist

    if event is None:
        event = get_event_by_id(db, event_id)
    # Never promote into an event that is not open (cancelled/suspended/completed/archived/draft).
    if event is None or event.status != "published" or not event.capacity:
        return
    try:
        max_capacity = int(float(str(event.capacity).strip()))
    except ValueError:
        return

    live_offers = db.query(EventWaitlist).filter(
        EventWaitlist.event_id == event_id,
        EventWaitlist.status == "payment_pending",
        sa.or_(
            EventWaitlist.payment_offer_expires_at.is_(None),
            EventWaitlist.payment_offer_expires_at > datetime.utcnow(),
        ),
    ).count()
    if _seats_taken(db, event_id) + live_offers >= max_capacity:
        return

    # Lock the oldest waitlist entry using with_for_update(skip_locked=True)
    next_in_line = (
        db.query(EventWaitlist)
        .filter(EventWaitlist.event_id == event_id, EventWaitlist.status == "waiting")
        .order_by(EventWaitlist.created_at.asc())
        .with_for_update(skip_locked=True)
        .first()
    )

    if not next_in_line:
        return

    # Verify they don't already have a confirmed registration (Phase A check)
    existing_reg = db.query(EventRegistration).filter(
        EventRegistration.event_id == event_id,
        sa.func.lower(EventRegistration.participant_email) == next_in_line.participant_email.strip().lower(),
        EventRegistration.status.in_(["confirmed", "attended"])
    ).first()

    if existing_reg:
        # If they somehow registered already, just mark waitlist as left and abort promotion
        next_in_line.status = "left"
        db.flush()
        return

    if _event_is_paid(event):
        # Set payment offer
        next_in_line.status = "payment_pending"
        next_in_line.payment_offer_expires_at = datetime.utcnow() + timedelta(minutes=15)
        _log_audit(
            db, event_id, "waitlist_offer", None,
            {"waitlist_id": str(next_in_line.id), "participant_email": next_in_line.participant_email, "expires_at": next_in_line.payment_offer_expires_at},
            changed_by="system:waitlist", commit=False,
        )
        db.flush()

        # We must trigger celery task in a way that respects the current transaction.
        # It's better for the caller to commit, but if we dispatch now it might run before commit.
        # But 15 minutes is plenty of time for the commit to finish.
        try:
            from app.tasks.event_tasks import expire_waitlist_offer_task
            expire_waitlist_offer_task.apply_async(
                args=[str(next_in_line.id)],
                countdown=15 * 60
            )
        except Exception:
            pass

        # Notify the person of the payment offer (notify_generic never existed, so this used to be
        # silently skipped by the broad except below — the offer was made but nobody was told).
        try:
            from app.services.notification_triggers import _safe_notify
            _safe_notify(
                db,
                "Spot Available!",
                f"A spot is now available for {event.title}. Complete payment within 15 minutes to secure your place.",
                "event_waitlist_offer",
                event.tenant_id,
                {"event_id": str(event_id), "waitlist_id": str(next_in_line.id)},
                participant_email=next_in_line.participant_email,
                channels=["in_app", "push", "email"],
            )
        except Exception:
            logger.warning("Waitlist offer notification failed for %s", next_in_line.participant_email, exc_info=True)

    else:
        # Auto-promote (Free flow)
        reg = EventRegistration(
            event_id=event_id,
            participant_name=next_in_line.participant_name,
            participant_email=next_in_line.participant_email,
            status="confirmed",
            qr_code=str(uuid.uuid4())[:12].upper(),
        )
        db.add(reg)
        db.flush() # ensure reg.id is available

        # Update waitlist entry instead of deleting
        next_in_line.status = "promoted"
        next_in_line.registration_id = reg.id
        _log_audit(
            db, event_id, "waitlist_promoted", None,
            {"waitlist_id": str(next_in_line.id), "registration_id": str(reg.id), "participant_email": next_in_line.participant_email},
            changed_by="system:waitlist", commit=False,
        )
        db.flush()

        # The caller manages the db.commit() for the transaction.

        # Notify
        try:
            from app.services.notification_triggers import notify_registration_confirmation
            notify_registration_confirmation(db, event, reg)
        except Exception:
            pass


# ---- Event Auto-Complete ----


def auto_complete_past_events_service(
    db: Session,
    enterprise_id: UUID | None = None,
    *,
    tenant_id: UUID | str | None = None,
    current_user: dict | None = None,
):
    """Transition published events past their end_date to completed.

    ``tenant_id`` (the caller's tenant) restricts the sweep to that tenant's events; only a
    Platform Super Admin call leaves it ``None`` and sweeps every tenant.
    """
    from app.models.event_model import Event as Ev
    from datetime import datetime
    now = datetime.utcnow()
    q = db.query(Ev).filter(
        Ev.status == "published",
        Ev.end_date < now,
        Ev.is_deleted.is_(False)
    )
    if tenant_id is not None:
        owned = event_tenant_clause(tenant_id)
        if owned is None:
            raise HTTPException(status_code=403, detail="Tenant could not be resolved for this caller")
        q = q.filter(owned)
    if enterprise_id:
        q = q.filter(Ev.enterprise_id == enterprise_id)
    events = q.all()
    updated = 0
    for ev in events:
        previous = ev.status
        ev.status = "completed"
        _log_audit(db, ev.id, "auto_complete", {"status": previous}, {"status": "completed"}, changed_by=_actor_id(current_user), commit=False)
        updated += 1
    if updated:
        db.commit()
    return {"auto_completed": updated}


# ---- Check-in / Uncheck-in / Check-out / QR Validation ----


def _find_registration(db: Session, event_id: UUID, registration_id: UUID | None, qr_code: str | None):
    """Find a registration by ID or QR code within an event."""
    from app.models.event_aux_models import EventRegistration

    if registration_id:
        reg = db.query(EventRegistration).filter(
            EventRegistration.id == registration_id,
            EventRegistration.event_id == event_id,
        ).first()
    elif qr_code:
        reg = db.query(EventRegistration).filter(
            EventRegistration.qr_code == qr_code,
            EventRegistration.event_id == event_id,
        ).first()
    else:
        raise HTTPException(status_code=400, detail="registration_id or qr_code is required")
    return reg


def check_in_service(db: Session, event_id: UUID, payload, current_user: dict | None = None, *, commit: bool = True):
    """Check-in a participant by registration_id or qr_code.

    ``commit=False`` only stages the check-in (and its audit row) in the caller's transaction, so a caller that
    also created the registration (walk-in) can commit everything atomically. The default is unchanged.
    """
    from app.models.event_aux_models import EventRegistration
    from app.models.event_model import Event as Ev
    from app.schemas.event_schema import EventCheckInResponse

    reg = _find_registration(db, event_id, payload.registration_id, payload.qr_code)
    if not reg:
        raise HTTPException(status_code=404, detail="Registration not found")

    ev_chk = db.query(Ev).filter(Ev.id == event_id).first()
    if ev_chk and ev_chk.status in ["cancelled", "completed", "archived", "suspended"]:
        raise HTTPException(status_code=400, detail=f"Cannot check-in: event is {ev_chk.status}")
    _validate_session_for_event(ev_chk, payload.session_id)
    if reg.status == "cancelled":
        raise HTTPException(status_code=400, detail="Cannot check-in: registration is cancelled")
    if reg.status == "attended":
        return EventCheckInResponse(
            message="Already checked in",
            registration_id=reg.id,
            participant_name=reg.participant_name,
            participant_email=reg.participant_email,
            status=reg.status,
            checked_in_at=reg.checked_in_at.isoformat() if reg.checked_in_at else None,
            session_id=payload.session_id,
        )
    if _registration_is_refunded(db, reg):
        raise HTTPException(status_code=400, detail="Cannot check-in: registration was refunded")

    reg.status = "attended"
    reg.checked_in_at = datetime.utcnow()
    reg.checked_in_by = _actor_uuid(current_user)
    if payload.session_id:
        reg.session_id = payload.session_id
    _log_audit(
        db, event_id, "check_in", {"registration_id": str(reg.id), "status": "confirmed"},
        {"registration_id": str(reg.id), "participant_email": reg.participant_email, "status": "attended", "session_id": payload.session_id, "method": getattr(payload, "method", None)},
        changed_by=_actor_id(current_user), commit=False,
    )
    if commit:
        db.commit()
        db.refresh(reg)
    else:
        db.flush()

    return EventCheckInResponse(
        message="Checked in successfully",
        registration_id=reg.id,
        participant_name=reg.participant_name,
        participant_email=reg.participant_email,
        status=reg.status,
        checked_in_at=reg.checked_in_at.isoformat() if reg.checked_in_at else None,
        session_id=reg.session_id,
    )


def uncheck_in_service(db: Session, event_id: UUID, payload, current_user: dict | None = None):
    """Undo a check-in, restoring registration to 'confirmed'."""
    from app.schemas.event_schema import EventUncheckInResponse

    reg = _find_registration(db, event_id, payload.registration_id, payload.qr_code)
    if not reg:
        raise HTTPException(status_code=404, detail="Registration not found")
    if reg.status != "attended":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot undo: registration status is '{reg.status}', not 'attended'",
        )

    reg.status = "confirmed"
    reg.checked_in_at = None
    reg.checked_in_by = None
    reg.checked_out_at = None  # a participant who is not checked in cannot be checked out
    reg.session_id = None
    _log_audit(
        db, event_id, "uncheck_in", {"registration_id": str(reg.id), "status": "attended"},
        {"registration_id": str(reg.id), "participant_email": reg.participant_email, "status": "confirmed"},
        changed_by=_actor_id(current_user), notes=getattr(payload, "reason", None), commit=False,
    )
    db.commit()
    db.refresh(reg)

    return EventUncheckInResponse(
        message="Check-in undone",
        registration_id=reg.id,
        participant_name=reg.participant_name,
        participant_email=reg.participant_email,
        status=reg.status,
        restored_to="confirmed",
    )


def check_out_service(db: Session, event_id: UUID, payload, current_user: dict | None = None):
    """Check-out a participant by registration_id or qr_code."""
    from app.schemas.event_schema import EventCheckOutResponse

    reg = _find_registration(db, event_id, payload.registration_id, payload.qr_code)
    if not reg:
        raise HTTPException(status_code=404, detail="Registration not found")
    if reg.status == "cancelled":
        raise HTTPException(status_code=400, detail="Cannot check-out: registration is cancelled")
    if reg.status == "no_show":
        raise HTTPException(status_code=400, detail="Cannot check-out: registration is marked as no_show")
    if reg.status != "attended" or not reg.checked_in_at:
        raise HTTPException(status_code=400, detail="Cannot check-out: participant has not been checked in")
    if reg.checked_out_at:
        return EventCheckOutResponse(
            message="Already checked out",
            registration_id=reg.id,
            participant_name=reg.participant_name,
            participant_email=reg.participant_email,
            status=reg.status,
            checked_in_at=reg.checked_in_at.isoformat() if reg.checked_in_at else None,
            checked_out_at=reg.checked_out_at.isoformat() if reg.checked_out_at else None,
            session_id=reg.session_id,
        )

    reg.checked_out_at = datetime.utcnow()
    _log_audit(
        db, event_id, "check_out", None,
        {"registration_id": str(reg.id), "participant_email": reg.participant_email},
        changed_by=_actor_id(current_user), commit=False,
    )
    db.commit()
    db.refresh(reg)

    return EventCheckOutResponse(
        message="Checked out successfully",
        registration_id=reg.id,
        participant_name=reg.participant_name,
        participant_email=reg.participant_email,
        status=reg.status,
        checked_in_at=reg.checked_in_at.isoformat() if reg.checked_in_at else None,
        checked_out_at=reg.checked_out_at.isoformat() if reg.checked_out_at else None,
        session_id=reg.session_id,
    )


def validate_qr_service(db: Session, event_id: UUID, qr_code: str | None):
    """Validate a QR code against event registrations."""
    from app.models.event_aux_models import EventRegistration
    from app.schemas.event_schema import EventQRValidateResponse

    if not qr_code:
        raise HTTPException(status_code=400, detail="qr_code is required")

    reg = db.query(EventRegistration).filter(
        EventRegistration.qr_code == qr_code,
        EventRegistration.event_id == event_id,
    ).first()

    if not reg:
        return EventQRValidateResponse(valid=False, message="QR code not found for this event")

    from app.models.event_model import Event
    event = db.query(Event).filter(Event.id == event_id).first()

    identity = dict(
        registration_id=reg.id,
        participant_name=reg.participant_name,
        participant_email=reg.participant_email,
        status=reg.status,
        event_id=event_id,
        event_title=event.title if event else None,
        ticket_type_id=reg.ticket_type_id,
    )
    # A cancelled (or refunded) registration is not a valid ticket, even though the QR still resolves.
    if reg.status == "cancelled":
        return EventQRValidateResponse(valid=False, message="Registration is cancelled", **identity)
    if _registration_is_refunded(db, reg):
        return EventQRValidateResponse(valid=False, message="Registration was refunded", **identity)

    return EventQRValidateResponse(
        valid=True,
        message=f"Valid ticket: {reg.participant_name} ({reg.status})",
        **identity,
    )


# ---- My Registrations ----


def my_registrations_service(db: Session, email: str, status_filter: str | None = None):
    """List registrations for a user by email, with bulk event lookup.

    Phase 2.8: also exposes each registration's meal/accommodation selections, with a purchase-time price/
    currency snapshot and ``status`` ("confirmed" when an EventRegistrationOption row backs the selection —
    i.e. it went through checkout/registration/walk-in after Phase 2.8 — else "selected" for a pre-2.8
    selection with no such row). This is the fix for the gap Mobile's Meals/Accommodation phases documented:
    until now, no participant-readable endpoint returned a registration's confirmed purchased options at all;
    the authenticated ticket/QR screen had nothing to read them from.
    """
    from app.models.event_aux_models import EventRegistration, EventRegistrationOption
    from app.models.event_model import Event
    from app.services.event_accommodation_service import attendee_accommodation_selections
    from app.services.event_meal_service import attendee_meal_selections
    from app.utils.event_accommodation import stored_accommodation_options
    from app.utils.event_meals import stored_options

    # Registrations store the email lower-cased, so compare case-insensitively (mixed-case token emails).
    q = db.query(EventRegistration).filter(sa.func.lower(EventRegistration.participant_email) == email.strip().lower())
    if status_filter:
        q = q.filter(EventRegistration.status == status_filter)
    regs = q.order_by(EventRegistration.created_at.desc()).all()

    event_ids = [r.event_id for r in regs]
    ev_map = (
        {e.id: e for e in db.query(Event).filter(Event.id.in_(event_ids)).all()}
        if event_ids
        else {}
    )

    # One bulk query for every registration's purchased option rows (no N+1 as the list grows).
    reg_ids = [r.id for r in regs]
    purchased_by_reg: dict = {}
    if reg_ids:
        for row in db.query(EventRegistrationOption).filter(EventRegistrationOption.registration_id.in_(reg_ids)).all():
            purchased_by_reg.setdefault(row.registration_id, {"meal": {}, "accommodation": {}})[row.option_type][row.option_id] = row

    out = []
    for r in regs:
        ev = ev_map.get(r.event_id)
        purchased = purchased_by_reg.get(r.id, {"meal": {}, "accommodation": {}})
        out.append({
            "registration_id": str(r.id),
            "event_id": str(r.event_id),
            "event_title": ev.title if ev else None,
            "event_status": ev.status if ev else None,
            "event_start": ev.start_date.isoformat() if ev and ev.start_date else None,
            "registration_status": r.status,
            "qr_code": r.qr_code,
            "checked_in_at": r.checked_in_at.isoformat() if r.checked_in_at else None,
            "meal_selections": (
                [s.model_dump() for s in attendee_meal_selections(stored_options(ev), r.meal_selections, purchased["meal"])]
                if ev else []
            ),
            "accommodation_selections": (
                [s.model_dump() for s in attendee_accommodation_selections(
                    stored_accommodation_options(ev), r.accommodation_selections, purchased["accommodation"])]
                if ev else []
            ),
        })
    return out


# ---- Template CRUD ----


def list_templates_service(db: Session, current_user: dict | None = None, access_token: str | None = None):
    from app.models.event_aux_models import EventTemplate
    q = db.query(EventTemplate)
    if is_platform_super_admin(current_user):
        return q.all()
    # Fail closed: an unresolvable tenant must never widen the list to every tenant's templates.
    tenant_id = resolve_caller_tenant_id(db, current_user, access_token=access_token)
    tenant_uuid = None
    try:
        tenant_uuid = UUID(str(tenant_id)) if tenant_id else None
    except ValueError:
        tenant_uuid = None
    if tenant_uuid is None:
        raise HTTPException(status_code=403, detail="Tenant could not be resolved for this caller")
    return q.filter(
        sa.or_(
            EventTemplate.tenant_id == tenant_uuid,
            sa.and_(
                EventTemplate.tenant_id.is_(None),
                EventTemplate.enterprise_id.in_(sa.select(Enterprise.id).where(Enterprise.tenant_id == tenant_uuid)),
            ),
        )
    ).all()


def apply_template_service(db: Session, template_id: UUID, payload: dict, current_user: dict | None = None, access_token: str | None = None):
    from app.models.event_aux_models import EventTemplate
    from app.models.event_model import Event as Ev
    import copy
    import uuid

    tmpl = db.query(EventTemplate).filter(EventTemplate.id == template_id).first()
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found")

    # Tenant isolation (fail closed): the template must belong to the caller's tenant, resolved
    # from the token / database — never from a request payload.
    auth_tenant_id = _assert_template_access(db, tmpl, current_user, access_token)
    supplied_tenant_id = payload.get("tenant_id")
    _validate_payload_tenant_id(supplied_tenant_id, auth_tenant_id)

    from app.services.event_form_config_service import _resolve_active_form_configuration
    from app.services.event_template_mapping import map_template_values
    from app.models.event_form_config_model import EventFormConfigurationVersion
    raw = tmpl.template_data or {}
    core_values = raw.get("core_values", raw)
    if not isinstance(core_values, dict):
        raise HTTPException(400, "core_values must be an object")
    data = dict(core_values)

    # Resolve tenant_id: payload explicit > template > auth context (ownership boundary)
    effective_tenant_id = supplied_tenant_id or tmpl.tenant_id or auth_tenant_id
    # If no tenant_id yet, try enterprise lookup fallback (rare)
    if not effective_tenant_id and current_user and current_user.get("tenant_slug"):
        try:
            from app.models.enterprise_model import Enterprise
            ent = db.query(Enterprise).filter(Enterprise.slug == current_user.get("tenant_slug")).first()
            if ent and ent.tenant_id:
                effective_tenant_id = str(ent.tenant_id)
        except Exception:
            pass

    # Resolve enterprise_id: payload > template > auth context — OPTIONAL
    enterprise_id = payload.get("enterprise_id") or data.get("enterprise_id") or tmpl.enterprise_id
    if enterprise_id and effective_tenant_id:
        # enterprise_id can come from the request or from template JSON — it must be the caller's.
        _verify_enterprise_in_tenant(db, enterprise_id, effective_tenant_id)
    if not enterprise_id and current_user:
        enterprise_id = current_user.get("enterprise_id") or current_user.get("enterpriseId")
        if not enterprise_id and current_user.get("tenant_slug"):
            try:
                from app.models.enterprise_model import Enterprise
                ent = db.query(Enterprise).filter(Enterprise.slug == current_user.get("tenant_slug")).first()
                if ent:
                    enterprise_id = str(ent.id)
            except Exception:
                pass

    # If tenant still null but enterprise supplied, derive tenant from Enterprise.tenant_id; keep null if not found
    if not effective_tenant_id and enterprise_id:
        try:
            from app.models.enterprise_model import Enterprise as _EntForTenant
            _ent = db.query(_EntForTenant).filter(_EntForTenant.id == enterprise_id).first()
            if _ent and getattr(_ent, "tenant_id", None):
                effective_tenant_id = str(_ent.tenant_id)
        except Exception:
            pass  # keep null

    # Current form wins; source provenance is used only for compatibility.
    config, version = _resolve_active_form_configuration(db, effective_tenant_id)
    source_sections = None
    source_version_id = tmpl.configuration_version_id or raw.get("form_configuration_version_id")
    if source_version_id:
        source_version = db.query(EventFormConfigurationVersion).filter(
            EventFormConfigurationVersion.id == source_version_id,
        ).first()
        if not source_version:
            raise HTTPException(400, "Source form configuration version not found")
        source_sections = source_version.sections or []
    data = map_template_values(raw, version.sections or [], source_sections)
    data["form_configuration_id"] = config.id
    data["form_configuration_version_id"] = version.id
    # Explicit NULL avoids populating newly introduced fields with ORM defaults.
    from app.services.event_form_registry import REGISTRY_BY_KEY
    for key in REGISTRY_BY_KEY:
        if key not in data and key in Ev.__table__.columns:
            data[key] = sa.null()

    # enterprise_id is OPTIONAL — tenant ownership is the boundary
    data["enterprise_id"] = enterprise_id if enterprise_id else None

    # Propagate tenant_id for new Event
    data["tenant_id"] = effective_tenant_id

    data["status"] = "draft"

    # Regenerate session ids
    if isinstance(data.get("sessions"), list):
        cloned_sessions = copy.deepcopy(data["sessions"])
        for s in cloned_sessions:
            if isinstance(s, dict):
                s["id"] = str(uuid.uuid4())
                sd = s.get("session_date")
                if hasattr(sd, "isoformat"):
                    s["session_date"] = sd.isoformat()
        data["sessions"] = cloned_sessions

    # Filter data to only valid Event columns, coerce UUID fields
    try:
        valid_keys = {c.key for c in Ev.__table__.columns}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        # Ensure UUID fields are None or valid UUID string
        for uuid_field in ("tenant_id", "enterprise_id", "location_id"):
            if isinstance(filtered.get(uuid_field), str) and filtered[uuid_field] in ("", "null"):
                filtered[uuid_field] = None
        event = Ev(**filtered)
        db.add(event)
        db.commit()
        db.refresh(event)
    except Exception as e:
        db.rollback()
        # Convert DB constraint errors to 400 instead of 500
        msg = str(e)
        if "null value" in msg.lower() or "not-null" in msg.lower():
            raise HTTPException(status_code=400, detail=f"Missing required field for Event creation from template: {msg}")
        if "violates foreign key" in msg.lower():
            raise HTTPException(status_code=400, detail=f"Invalid reference in template data: {msg}")
        raise HTTPException(status_code=400, detail=f"Failed to create Event from template: {msg}")
    return event


def _is_owning_staff(db: Session, event, current_user: dict, access_token: str | None) -> bool:
    """True when the caller is staff of the Event's tenant (or an active Platform Super Admin)."""
    if current_user.get("role") not in (*STAFF_ROLES, "super_admin"):
        return False
    try:
        assert_event_access(db, event, current_user, access_token=access_token)
        return True
    except HTTPException:
        return False


def get_meeting_link_service(db: Session, event_id: UUID, current_user: dict, access_token: str | None = None):
    """Get meeting link — staff of the owning tenant, or a registered participant only."""
    from app.models.event_aux_models import EventRegistration
    import sqlalchemy as sa

    ev = _get_event_or_404(db, event_id)
    email = current_user.get("email") or ""

    if not _is_owning_staff(db, ev, current_user, access_token):
        reg = db.query(EventRegistration).filter(
            EventRegistration.event_id == event_id,
            sa.func.lower(EventRegistration.participant_email) == email.strip().lower(),
            EventRegistration.status.in_(["confirmed", "attended"]),
        ).first()
        if not reg:
            raise HTTPException(status_code=403, detail="Only registered participants can access meeting link")
            
    return {
        "event_id": str(event_id),
        "meeting_link": ev.meeting_link,
        "meeting_provider": ev.meeting_provider,
        "delivery_mode": ev.delivery_mode,
    }


def get_session_meeting_link_service(db: Session, event_id: UUID, session_id: str, current_user: dict, access_token: str | None = None):
    """Get meeting link for a specific session — admin/provider or registered participant only."""
    from app.models.event_aux_models import EventRegistration
    import sqlalchemy as sa

    ev = _get_event_or_404(db, event_id)
    
    # Verify session exists
    session = next((s for s in (ev.sessions or []) if isinstance(s, dict) and s.get("id") == session_id), None)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
        
    email = current_user.get("email") or ""

    if not _is_owning_staff(db, ev, current_user, access_token):
        # We need to check if the user is registered for the event.
        # If the event requires session-specific registration, we might need to check that, 
        # but the prompt says: "Authorization must be equivalent in principle to the protected event meeting-link endpoint."
        # Wait, the prompt also says: "Authenticated registered for a specific session: GET protected session meeting endpoint -> authorized session URL."
        # If there's per-session registration, `session_id` on the registration might be set.
        reg_query = db.query(EventRegistration).filter(
            EventRegistration.event_id == event_id,
            sa.func.lower(EventRegistration.participant_email) == email.strip().lower(),
            EventRegistration.status.in_(["confirmed", "attended"]),
        )
        
        regs = reg_query.all()
        if not regs:
            raise HTTPException(status_code=403, detail="Only registered participants can access meeting link")
            
        # Check if they are registered for this specific session, if session-specific registration is enforced
        # Wait, the existing code:
        # Some registrations might have session_id set. If the event is per-session, then reg.session_id must match.
        # Let's see if any registration allows it.
        has_access = False
        for reg in regs:
            if not reg.session_id or reg.session_id == session_id:
                has_access = True
                break
                
        if not has_access:
            raise HTTPException(status_code=403, detail="Not registered for this specific session")
            
    return {
        "event_id": str(event_id),
        "session_id": session_id,
        "meeting_provider": ev.meeting_provider, # sessions don't currently have individual providers in the schema, they use event's provider
        "meeting_link": session.get("meeting_link"),
    }


# ---- Contact Organiser ----
def contact_organiser_service(db: Session, event_id: UUID, payload: dict, current_user: dict):
    ev = _get_event_or_404(db, event_id)
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
    msg = payload.get("message") or payload.get("text") or ""
    if not msg:
        raise HTTPException(status_code=400, detail="message is required")
    try:
        from app.services.notification_triggers import _safe_notify
        _safe_notify(
            db, f"Message for {ev.title}", msg, "event_contact", ev.tenant_id,
            {"event_id": str(event_id), "from_email": current_user.get("email"), "from_name": current_user.get("name")},
            participant_email=ev.organiser_contact,
        )
    except Exception:
        pass
    return {"message": "Message sent to organiser", "event_id": str(event_id), "organiser": ev.organiser_contact}


# ---- Event Order Status & Refund Approval ----

def update_event_order_status_service(db: Session, event_id: UUID, order_id: UUID, payload, current_user: dict | None = None):
    from app.models.event_aux_models import EventOrder
    VALID_TRANSITIONS = {
        "confirmed": ["cancelled", "completed"],
        "refund_requested": ["refunded", "cancelled"],
        "cancelled": [],
        "refunded": [],
        "completed": [],
    }
    order = db.query(EventOrder).filter(EventOrder.id == order_id, EventOrder.event_id == event_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    new_status = payload.status
    allowed = VALID_TRANSITIONS.get(order.status, [])
    if new_status not in allowed:
        raise HTTPException(status_code=400, detail=f"Cannot transition from '{order.status}' to '{new_status}'. Allowed: {allowed}")

    # An order that ends refunded/cancelled must not leave its registration valid: release the seat
    # and invalidate the QR. (EventOrder has no registration FK — see _active_registration_for_order.)
    reg = None
    if new_status in ("refunded", "cancelled"):
        reg = _active_registration_for_order(db, order)
        if reg is not None and reg.status == "attended":
            raise HTTPException(status_code=400, detail=f"Cannot mark order {new_status}: participant already attended")
    previous = order.status
    order.status = new_status
    if new_status == "refunded":
        order.payment_status = "refunded"
    _log_audit(
        db, event_id, "order_status",
        {"order_id": str(order.id), "status": previous},
        {"order_id": str(order.id), "status": new_status},
        changed_by=_actor_id(current_user), notes=getattr(payload, "reason", None), commit=False,
    )
    if reg is not None:
        _cancel_registration_and_release_seat(
            db, _get_event_or_404(db, event_id), reg,
            actor=_actor_id(current_user), audit_action="registration_cancel",
            notes=f"order {new_status}",
        )
    db.commit()
    db.refresh(order)
    return {"id": str(order.id), "status": order.status, "message": f"Order status updated to '{new_status}'"}


def approve_event_refund_service(db: Session, event_id: UUID, order_id: UUID, payload, current_user: dict | None = None):
    from app.models.event_aux_models import EventOrder
    order = db.query(EventOrder).filter(EventOrder.id == order_id, EventOrder.event_id == event_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != "refund_requested":
        raise HTTPException(status_code=400, detail=f"Order is not in refund_requested state (current: {order.status})")
    action = payload.action
    actor = _actor_id(current_user)
    if action == "approve":
        reg = _active_registration_for_order(db, order)
        if reg is not None and reg.status == "attended":
            raise HTTPException(status_code=400, detail="Cannot approve refund: participant already attended")
        order.status = "refunded"
        order.payment_status = "refunded"
        _log_audit(
            db, event_id, "refund",
            {"order_id": str(order.id), "status": "refund_requested"},
            {"order_id": str(order.id), "status": "refunded", "decision": "approved"},
            changed_by=actor, notes=getattr(payload, "reason", None), commit=False,
        )
        # Never mark an order refunded while its registration stays valid.
        if reg is not None:
            _cancel_registration_and_release_seat(
                db, _get_event_or_404(db, event_id), reg,
                actor=actor, audit_action="registration_cancel", notes="refund approved",
            )
        message = "Refund approved"
    elif action == "reject":
        order.status = "confirmed"
        order.payment_status = "confirmed"
        order.refund_reason = None
        _log_audit(
            db, event_id, "refund",
            {"order_id": str(order.id), "status": "refund_requested"},
            {"order_id": str(order.id), "status": "confirmed", "decision": "rejected"},
            changed_by=actor, notes=getattr(payload, "reason", None), commit=False,
        )
        message = "Refund rejected — order restored to confirmed"
    else:
        raise HTTPException(status_code=400, detail="action must be approve|reject")
    db.commit()
    db.refresh(order)
    return {"id": str(order.id), "status": order.status, "payment_status": order.payment_status, "message": message}
