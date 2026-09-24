import logging
from dataclasses import dataclass, field
from uuid import UUID

import sqlalchemy as sa
from fastapi import HTTPException, status
from sqlalchemy.orm import Session, joinedload

from app.models.enterprise_model import Enterprise
from app.models.event_model import Event
from app.repository.query_utils import (
    apply_ilike_search,
    apply_soft_delete_filter,
    paginate_query,
)

logger = logging.getLogger(__name__)

# Roles that manage events on behalf of a tenant. "admin" (tenant owner) is
# tenant-scoped, never platform-wide — only an active "super_admin" crosses tenants.
STAFF_ROLES = ("admin", "provider")

# Fields that must never be assigned through a generic Event update.
_PROTECTED_UPDATE_FIELDS = frozenset({"id", "tenant_id", "enterprise_id", "is_deleted", "created_at"})


# ---------------------------------------------------------------------------
# Ownership / tenant isolation
# ---------------------------------------------------------------------------


def _norm(value) -> str | None:
    if value is None or value == "":
        return None
    return str(value).strip().lower()


def _as_uuid(value) -> UUID | None:
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def is_platform_super_admin(current_user: dict | None) -> bool:
    """True only for an active Platform Super Admin identity (claim-based, no network)."""
    from app.services.super_admin_identity import profile_status_is_active

    return bool(current_user) and current_user.get("role") == "super_admin" and profile_status_is_active(current_user)


def event_owner_tenant_id(event) -> str | None:
    """Owning tenant of an Event.

    ``Event.tenant_id`` when present; legacy rows that predate it fall back to the
    owning Enterprise's tenant. Anything else has no resolvable owner (fail closed).
    """
    tenant_id = _norm(getattr(event, "tenant_id", None))
    if tenant_id:
        return tenant_id
    enterprise = getattr(event, "enterprise", None)
    if enterprise is not None:
        return _norm(getattr(enterprise, "tenant_id", None))
    return None


def assert_event_access(db: Session, event, current_user: dict | None, *, access_token: str | None = None):
    """Authorize ``current_user`` to manage an already-loaded Event, or raise 403.

    - Active Platform Super Admin: allowed across tenants.
    - ``admin`` / ``provider``: tenant-scoped. The caller's tenant is resolved from the
      token, then from the database (WebAuth sessions often carry no tenant claim), and
      must equal the Event's owning tenant. An unresolvable tenant is a denial, never a
      pass. A tenant supplied in a request payload is never consulted.
    - Anyone else: denied.
    """
    from app.core.auth_context import resolve_auth_tenant_id_with_db

    if is_platform_super_admin(current_user):
        return event
    role = current_user.get("role") if current_user else None
    if role not in STAFF_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    caller_tenant = _norm(resolve_auth_tenant_id_with_db(db, current_user, access_token=access_token))
    owner_tenant = event_owner_tenant_id(event)
    if not caller_tenant or not owner_tenant or caller_tenant != owner_tenant:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this tenant")
    return event


def require_event_owner(
    db: Session,
    event_id: UUID,
    current_user: dict,
    *,
    access_token: str | None = None,
    include_deleted: bool = False,
):
    """Management lookup: 404 if the Event does not exist, 403 unless the caller owns it.

    Public / participant reads intentionally do not use this (see ``can_view_event``).
    """
    event = get_event_by_id(db, event_id, include_deleted=include_deleted)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return assert_event_access(db, event, current_user, access_token=access_token)


def resolve_caller_tenant_id(db: Session, current_user: dict | None, *, access_token: str | None = None) -> str | None:
    """Tenant of an authenticated staff caller (token, then DB, then live lookup), else None."""
    from app.core.auth_context import resolve_auth_tenant_id_with_db

    if not current_user or current_user.get("role") not in STAFF_ROLES:
        return None
    return _norm(resolve_auth_tenant_id_with_db(db, current_user, access_token=access_token))


# ---------------------------------------------------------------------------
# Read visibility (public / participant / staff)
# ---------------------------------------------------------------------------


@dataclass
class EventViewer:
    """Who is reading events. The default (no user) is the anonymous public viewer."""

    user: dict | None = None
    email: str | None = None
    role: str | None = None
    tenant_id: str | None = None  # resolved only for admin/provider
    is_platform: bool = False
    _remote_checked: bool = field(default=False, repr=False)


def build_event_viewer(
    db: Session,
    current_user: dict | None,
    *,
    access_token: str | None = None,
    remote_platform_check: bool = False,
) -> EventViewer:
    """Classify the reader of a public/participant event endpoint.

    ``remote_platform_check`` additionally asks the identity service whether the caller
    is a Platform Super Admin when the token carries no such claim. It is only requested
    when the request would otherwise hide non-public events, so ordinary customer traffic
    never triggers a remote call.
    """
    if not current_user:
        return EventViewer()
    role = current_user.get("role")
    viewer = EventViewer(
        user=current_user,
        email=_norm(current_user.get("email")),
        role=role,
    )
    if is_platform_super_admin(current_user):
        viewer.is_platform = True
        return viewer
    viewer.tenant_id = resolve_caller_tenant_id(db, current_user, access_token=access_token)
    if remote_platform_check:
        _remote_platform_check(viewer, access_token)
    return viewer


def _remote_platform_check(viewer: EventViewer, access_token: str | None) -> None:
    if viewer.is_platform or viewer._remote_checked or not viewer.user or viewer.role == "customer":
        return
    viewer._remote_checked = True
    from app.services.super_admin_identity import resolve_platform_super_admin_user

    try:
        if resolve_platform_super_admin_user(viewer.user, access_token=access_token):
            viewer.is_platform = True
    except Exception:
        logger.warning("Platform super-admin lookup failed; treating caller as non-platform", exc_info=True)


def viewer_owns_event(event, viewer: EventViewer) -> bool:
    """True for a Platform Super Admin or staff of the Event's owning tenant."""
    if viewer.is_platform:
        return True
    owner = event_owner_tenant_id(event)
    return bool(viewer.tenant_id and owner and viewer.tenant_id == owner)


def user_has_event_relationship(db: Session, event_id: UUID, email: str | None) -> bool:
    """True when ``email`` has ever registered for, or joined the waitlist of, the Event.

    Any registration status counts (confirmed/attended/cancelled), so a participant can
    still open a cancelled or completed event they hold a ticket for.
    """
    from app.models.event_aux_models import EventRegistration, EventWaitlist

    if not email:
        return False
    email = email.strip().lower()
    if (
        db.query(EventRegistration.id)
        .filter(EventRegistration.event_id == event_id, sa.func.lower(EventRegistration.participant_email) == email)
        .first()
    ):
        return True
    return (
        db.query(EventWaitlist.id)
        .filter(EventWaitlist.event_id == event_id, sa.func.lower(EventWaitlist.participant_email) == email)
        .first()
        is not None
    )


def can_view_event(db: Session, event, viewer: EventViewer, *, access_token: str | None = None) -> bool:
    """Published events are public. Everything else needs ownership or a participant relationship."""
    if getattr(event, "status", None) == "published":
        return True
    if viewer_owns_event(event, viewer):
        return True
    if viewer.user is not None and user_has_event_relationship(db, event.id, viewer.email):
        return True
    if viewer.user is not None:
        _remote_platform_check(viewer, access_token)
        return viewer.is_platform
    return False


def event_tenant_clause(tenant_id):
    """SQL predicate for "Events owned by ``tenant_id``" (None if the id is not a valid UUID).

    Mirrors ``event_owner_tenant_id``: ``Event.tenant_id``, or — for legacy rows that
    predate it — the owning Enterprise's tenant.
    """
    tenant_uuid = _as_uuid(tenant_id)
    if not tenant_uuid:
        return None
    return sa.or_(
        Event.tenant_id == tenant_uuid,
        sa.and_(
            Event.tenant_id.is_(None),
            Event.enterprise_id.in_(sa.select(Enterprise.id).where(Enterprise.tenant_id == tenant_uuid)),
        ),
    )


def event_visibility_filter(viewer: EventViewer):
    """SQL predicate limiting a list to what ``viewer`` may see (None = unrestricted)."""
    if viewer.is_platform:
        return None
    clauses = [Event.status == "published"]
    owned = event_tenant_clause(viewer.tenant_id)
    if owned is not None:
        clauses.append(owned)
    return sa.or_(*clauses)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create_event(db: Session, event_data):
    payload = event_data.to_model_data() if hasattr(event_data, "to_model_data") else event_data.model_dump()
    event = Event(**payload)
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def get_events(
    db: Session,
    *,
    search: str | None = None,
    category: str | None = None,
    tenant_id: UUID | None = None,
    enterprise_id: UUID | None = None,
    location_id: UUID | None = None,
    status: str | None = None,
    delivery_mode: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_price: str | None = None,
    max_price: str | None = None,
    page: int = 1,
    page_size: int = 20,
    include_deleted: bool = False,
    viewer: EventViewer | None = None,
):
    """List events visible to ``viewer`` (default: the anonymous public viewer = published only)."""
    from datetime import datetime
    query = db.query(Event).options(joinedload(Event.enterprise))
    query = apply_soft_delete_filter(query, Event, include_deleted)

    visibility = event_visibility_filter(viewer or EventViewer())
    if visibility is not None:
        query = query.filter(visibility)

    if tenant_id:
        query = query.filter(Event.tenant_id == tenant_id)
    if enterprise_id:
        query = query.filter(Event.enterprise_id == enterprise_id)
    if location_id:
        query = query.filter(Event.location_id == location_id)
    if category:
        query = query.filter(Event.category == category)
    if status:
        query = query.filter(Event.status == status)
    if delivery_mode:
        query = query.filter(Event.delivery_mode == delivery_mode)
    if date_from:
        try:
            dt = datetime.fromisoformat(date_from.replace("Z",""))
            query = query.filter(Event.start_date >= dt)
        except Exception:
            pass
    if date_to:
        try:
            dt = datetime.fromisoformat(date_to.replace("Z",""))
            query = query.filter(Event.end_date <= dt)
        except Exception:
            pass
    if min_price or max_price:
        # price is string, try cast
        try:
            import sqlalchemy as sa
            if min_price:
                query = query.filter(sa.cast(Event.price, sa.Float) >= float(min_price))
            if max_price:
                query = query.filter(sa.cast(Event.price, sa.Float) <= float(max_price))
        except Exception:
            pass
    if search:
        query = apply_ilike_search(
            query,
            [Event.title, Event.description, Event.category, Event.subcategory],
            search,
        )

    query = query.order_by(Event.created_at.desc())
    return paginate_query(query, page, page_size)


def get_event_by_id(db: Session, event_id: UUID, include_deleted: bool = False):
    query = db.query(Event).options(joinedload(Event.enterprise)).filter(Event.id == event_id)
    if not include_deleted:
        query = apply_soft_delete_filter(query, Event, include_deleted)
    return query.first()


def update_event(db: Session, event, update_data, commit: bool = True):
    payload = update_data.to_model_data() if hasattr(update_data, "to_model_data") else update_data.model_dump(exclude_unset=True)

    # Ownership and identity are never editable through a generic update — in
    # particular a caller must not be able to move an Event to another tenant.
    for protected in _PROTECTED_UPDATE_FIELDS:
        if protected in payload:
            if protected == "tenant_id" and _norm(payload["tenant_id"]) != _norm(getattr(event, "tenant_id", None)):
                logger.warning("Ignored tenant_id change on Event %s via update", getattr(event, "id", None))
            payload.pop(protected)

    # Preserve meeting links if they are masked as "protected"
    if "meeting_link" in payload and payload["meeting_link"] == "protected":
        payload.pop("meeting_link")

    if "sessions" in payload and payload["sessions"] is not None:
        old_sessions = {str(s.get("id")): s for s in (event.sessions or []) if isinstance(s, dict)}
        for s in payload["sessions"]:
            if s.get("meeting_link") == "protected":
                old_s = old_sessions.get(str(s.get("id")))
                if old_s and "meeting_link" in old_s:
                    s["meeting_link"] = old_s["meeting_link"]
                else:
                    s.pop("meeting_link", None)

    for key, value in payload.items():
        setattr(event, key, value)
    if commit:
        db.commit()
        db.refresh(event)
    return event


def delete_event(db: Session, event):
    event.is_deleted = True
    event.status = "archived"
    db.commit()
    db.refresh(event)
    return event
