"""Virtual meeting providers: platform switches, a business's connections, and the meeting links of events, event sessions and training sessions.

The platform creates and shares the link. It does not host the call, record it or track who joined.

A "target" is anything that can have a meeting: an event (one meeting for the whole event), one session of an event, or one live session of
a training. Rules this module keeps for every target:
  * A pasted (manual) link always wins over a generated one. A link already on the listing that this module did not create counts as pasted.
  * Connecting a provider only makes it available. A target uses it only when the merchant chose it (or the business default) and
    automatic generation is on.
  * A failure to create a meeting never fails the booking, registration or publish. It is recorded, retried a few times in the
    background and shown to the merchant, who can retry or paste a link.
  * Tokens are encrypted, refreshed when they are about to expire, and never returned, logged or put in a notification.
"""
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Callable
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.meeting_model import MeetingAudit, MeetingConnection, MeetingLink, MeetingMerchantSettings, MeetingProviderSetting
from app.services import meeting_crypto
from app.services.meeting_providers import PROVIDER_KEYS, get_provider
from app.services.meeting_providers.base import MeetingSpec, ProviderAuthError, ProviderError, Tokens

logger = logging.getLogger(__name__)

LABELS = {"zoom": "Zoom", "google_meet": "Google Meet", "teams": "Microsoft Teams"}
VIRTUAL_MODES = ("online", "hybrid")
KIND_EVENT, KIND_EVENT_SESSION, KIND_TRAINING_SESSION = "event", "event_session", "training_session"
RESOURCE_EVENT = KIND_EVENT
VISIBLE_EVENT_STATUSES = ("published", "completed")
REGISTRATION_STATUSES = ("confirmed", "attended")
TRAINING_VISIBLE_STATUSES = ("published", "completed")
TRAINING_ENROLMENT_STATUSES = ("enrolled", "active", "completed", "approved", "attended")
# the training module's own names for the providers
TRAINING_PROVIDER_NAMES = {"zoom": "zoom", "google_meet": "meet", "teams": "teams"}
_REFRESH_MARGIN = timedelta(seconds=90)


def _err(status: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail=detail)


def _now() -> datetime:
    return datetime.utcnow()


def _dispatch(fn, *args, **kwargs) -> None:
    def runner():
        try:
            fn(*args, **kwargs)
        except Exception:
            logger.exception("meeting notification failed")

    threading.Thread(target=runner, name="meeting-notification", daemon=True).start()


def _check_provider_key(key: str) -> str:
    if key not in PROVIDER_KEYS:
        raise _err(404, "Unknown meeting provider. Use zoom, google_meet or teams")
    return key


def audit(db: Session, action: str, *, tenant_id=None, provider=None, actor_id=None, resource_type=None, resource_id=None, details=None) -> None:
    db.add(MeetingAudit(tenant_id=tenant_id, provider=provider, actor_id=actor_id, resource_type=resource_type, resource_id=resource_id, action=action, details=details))


def _uuid(value) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except (ValueError, TypeError):
        return None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def _real_link(value) -> str | None:
    """A stored address, or None when it is empty or the 'protected' placeholder the event API shows in place of a link."""
    text_value = (value or "").strip()
    return None if not text_value or text_value.lower() == "protected" else text_value


# ---------------------------------------------------------------- platform switches (Super Admin)

def platform_enabled(db: Session, key: str) -> bool:
    row = db.get(MeetingProviderSetting, key)
    return True if row is None else bool(row.enabled)


def platform_providers(db: Session) -> list[dict]:
    return [{"provider": key, "label": LABELS[key], "enabled": platform_enabled(db, key), "configured": get_provider(key).configured(),
             "connected_businesses": db.query(MeetingConnection).filter(MeetingConnection.provider == key).count()} for key in PROVIDER_KEYS]


def set_platform_provider(db: Session, actor: dict, key: str, enabled: bool) -> dict:
    _check_provider_key(key)
    row = db.get(MeetingProviderSetting, key) or MeetingProviderSetting(provider=key)
    changed = row.enabled is None or bool(row.enabled) != enabled
    row.enabled, row.updated_by, row.updated_at = enabled, _uuid(actor.get("id")), _now()
    db.add(row)
    audit(db, "provider_enabled" if enabled else "provider_disabled", provider=key, actor_id=_uuid(actor.get("id")))
    db.commit()
    if changed and not enabled:
        owners = {c.connected_by for c in db.query(MeetingConnection).filter(MeetingConnection.provider == key).all() if c.connected_by}
        _dispatch(_notify_users, list(owners), "meeting_provider_disabled", f"{LABELS[key]} was switched off",
                  f"{LABELS[key]} was switched off for the platform. Existing meeting links keep working, but new ones cannot be created with it. Choose another provider.",
                  {"provider": key})
    return next(p for p in platform_providers(db) if p["provider"] == key)


# ---------------------------------------------------------------- a business's connections

def _redirect_uri(key: str, request_base: str | None) -> str:
    base = (settings.MEETING_OAUTH_REDIRECT_BASE.strip() or settings.PUBLIC_API_BASE_URL.strip() or (request_base or "")).rstrip("/")
    return f"{base}/api/v1/meetings/oauth/callback/{key}"


def _connection(db: Session, tenant_id, key: str) -> MeetingConnection | None:
    return db.query(MeetingConnection).filter(MeetingConnection.tenant_id == tenant_id, MeetingConnection.provider == key).first()


def merchant_settings(db: Session, tenant_id) -> MeetingMerchantSettings:
    row = db.get(MeetingMerchantSettings, tenant_id)
    if row is None:
        return MeetingMerchantSettings(tenant_id=tenant_id, default_provider=None, auto_generate=True)
    return row


def list_providers(db: Session, tenant_id) -> dict:
    prefs = merchant_settings(db, tenant_id)
    items = []
    for key in PROVIDER_KEYS:
        conn, provider, enabled = _connection(db, tenant_id, key), get_provider(key), platform_enabled(db, key)
        items.append({
            "provider": key, "label": LABELS[key], "platform_enabled": enabled, "configured": provider.configured(),
            "can_connect": enabled and provider.configured(),
            "status": "not_connected" if conn is None else conn.status,
            "account_email": conn.account_email if conn else None, "account_name": conn.account_name if conn else None,
            "connected_at": conn.connected_at if conn else None, "is_default": prefs.default_provider == key,
        })
    return {"items": items, "default_provider": prefs.default_provider, "auto_generate": bool(prefs.auto_generate)}


def start_connect(db: Session, user: dict, tenant_id, key: str, request_base: str | None) -> dict:
    _check_provider_key(key)
    provider = get_provider(key)
    if not platform_enabled(db, key):
        raise _err(409, f"{LABELS[key]} is switched off for the platform")
    if not provider.configured():
        raise _err(409, f"{LABELS[key]} is not set up on this platform yet. Ask the platform team to add its app credentials")
    state = meeting_crypto.sign_state({"t": str(tenant_id), "u": str(user.get("id")), "p": key, "n": uuid4().hex[:12]})
    return {"provider": key, "authorization_url": provider.authorize_url(state, _redirect_uri(key, request_base)), "expires_in_seconds": meeting_crypto.STATE_LIFETIME_SECONDS}


def finish_connect(db: Session, key: str, code: str | None, state: str | None, error: str | None, request_base: str | None) -> dict:
    """The provider sent the merchant back. Returns {"ok": bool, "provider": key, "reason": ...} for the redirect to the app; never raises."""
    data = meeting_crypto.read_state(state)
    if not data or data.get("p") != key:
        return {"ok": False, "provider": key, "reason": "invalid_state"}
    tenant_id, user_id = _uuid(data.get("t")), _uuid(data.get("u"))
    if error or not code:
        return {"ok": False, "provider": key, "reason": "denied" if error == "access_denied" else (error or "no_code")}
    try:
        provider = get_provider(key)
        if not platform_enabled(db, key) or not provider.configured():
            return {"ok": False, "provider": key, "reason": "provider_unavailable"}
        tokens, account = provider.exchange_code(code, _redirect_uri(key, request_base))
    except ProviderError as exc:
        return {"ok": False, "provider": key, "reason": exc.code}
    except Exception:
        logger.exception("meeting provider %s code exchange failed", key)
        return {"ok": False, "provider": key, "reason": "provider_error"}
    conn = _connection(db, tenant_id, key) or MeetingConnection(tenant_id=tenant_id, provider=key)
    conn.status, conn.last_error = "connected", None
    conn.account_id, conn.account_email, conn.account_name = account.account_id, account.email, account.name
    conn.access_token, conn.refresh_token = meeting_crypto.encrypt(tokens.access_token), meeting_crypto.encrypt(tokens.refresh_token)
    conn.token_expires_at, conn.scope, conn.connected_by, conn.connected_at = tokens.expires_at, tokens.scope, user_id, _now()
    db.add(conn)
    audit(db, "connected", tenant_id=tenant_id, provider=key, actor_id=user_id, details={"account": account.email})
    db.commit()
    return {"ok": True, "provider": key, "reason": None}


def disconnect(db: Session, user: dict, tenant_id, key: str) -> dict:
    _check_provider_key(key)
    conn = _connection(db, tenant_id, key)
    if conn is None:
        return {"provider": key, "status": "not_connected"}
    tokens = _stored_tokens(conn)
    try:
        if tokens:
            get_provider(key).revoke(tokens)
    except Exception:
        logger.warning("could not revoke %s access at the provider", key)
    # links created with this account keep their address; they are just no longer managed from here
    db.query(MeetingLink).filter(MeetingLink.tenant_id == tenant_id, MeetingLink.connection_id == conn.id).update({"connection_id": None}, synchronize_session=False)
    db.delete(conn)
    audit(db, "disconnected", tenant_id=tenant_id, provider=key, actor_id=_uuid(user.get("id")))
    db.commit()
    return {"provider": key, "status": "not_connected"}


def update_merchant_settings(db: Session, user: dict, tenant_id, default_provider, auto_generate, fields_set: set) -> dict:
    row = db.get(MeetingMerchantSettings, tenant_id) or MeetingMerchantSettings(tenant_id=tenant_id, auto_generate=True)
    if "default_provider" in fields_set:
        if default_provider is not None:
            _check_provider_key(default_provider)
            if _connection(db, tenant_id, default_provider) is None:
                raise _err(409, f"Connect {LABELS[default_provider]} before making it the default")
        row.default_provider = default_provider
    if "auto_generate" in fields_set and auto_generate is not None:
        row.auto_generate = bool(auto_generate)
    row.updated_by, row.updated_at = _uuid(user.get("id")), _now()
    db.add(row)
    audit(db, "settings_changed", tenant_id=tenant_id, actor_id=_uuid(user.get("id")), details={"default_provider": row.default_provider, "auto_generate": row.auto_generate})
    db.commit()
    return {"default_provider": row.default_provider, "auto_generate": bool(row.auto_generate)}


# ---------------------------------------------------------------- tokens

def _stored_tokens(conn: MeetingConnection) -> Tokens | None:
    access = meeting_crypto.decrypt(conn.access_token)
    if not access and not conn.refresh_token:
        return None
    return Tokens(access_token=access or "", refresh_token=meeting_crypto.decrypt(conn.refresh_token), expires_at=conn.token_expires_at, scope=conn.scope)


def _mark_reconnect(db: Session, conn: MeetingConnection, message: str | None = None) -> None:
    first = conn.status != "reconnect_needed"
    conn.status, conn.last_error = "reconnect_needed", (message or "The provider no longer accepts this connection")[:255]
    audit(db, "reconnect_needed", tenant_id=conn.tenant_id, provider=conn.provider)
    db.commit()
    if first and conn.connected_by:
        _dispatch(_notify_users, [conn.connected_by], "meeting_reconnect_needed", f"Reconnect {LABELS[conn.provider]}",
                  f"{LABELS[conn.provider]} no longer accepts your connection, so new meeting links cannot be created. Open the meeting settings and reconnect it.",
                  {"provider": conn.provider})


def _usable_tokens(db: Session, conn: MeetingConnection) -> Tokens:
    """Valid access for a call. Refreshes a token that is about to expire. Raises ProviderAuthError when the merchant must reconnect."""
    if conn.status != "connected":
        raise ProviderAuthError()
    tokens = _stored_tokens(conn)
    if tokens is None:
        _mark_reconnect(db, conn)
        raise ProviderAuthError()
    if tokens.access_token and (tokens.expires_at is None or tokens.expires_at - _now() > _REFRESH_MARGIN):
        return tokens
    if not tokens.refresh_token:
        _mark_reconnect(db, conn)
        raise ProviderAuthError()
    try:
        fresh = get_provider(conn.provider).refresh(tokens.refresh_token)
    except ProviderAuthError:
        _mark_reconnect(db, conn)
        raise
    conn.access_token, conn.token_expires_at = meeting_crypto.encrypt(fresh.access_token), fresh.expires_at
    if fresh.refresh_token and fresh.refresh_token != tokens.refresh_token:
        conn.refresh_token = meeting_crypto.encrypt(fresh.refresh_token)
    db.commit()
    return Tokens(access_token=fresh.access_token, refresh_token=fresh.refresh_token or tokens.refresh_token, expires_at=fresh.expires_at, scope=conn.scope)


# ---------------------------------------------------------------- targets: what can have a meeting

@dataclass
class Target:
    kind: str
    owner: object                         # the Event or the TrainingLiveSession
    resource_id: UUID
    scope: str                            # "" for an event or a training session; the session id for one session of an event
    tenant_id: UUID | None
    title: str
    description: str | None
    start: Callable[[], datetime | None]  # naive UTC
    end: Callable[[], datetime | None]
    virtual: bool                         # an in-person event has no meeting
    eligible: bool                        # published: the only time a link is created automatically
    stored_link: Callable[[], str | None]
    store: Callable[[str | None, str | None], None]   # (link, provider) -> write onto the listing itself
    notify_target: dict = field(default_factory=dict)  # {"event_id": ..} or {"training_id": ..}
    label: str = "meeting"
    not_virtual_message: str = "An in-person event has no meeting"

    def spec(self) -> MeetingSpec:
        start = self.start()
        if start is None:
            raise _err(422, f"Set the start date and time of this {self.label} before creating a meeting")
        end = self.end() or start + timedelta(hours=1)
        if end <= start:
            end = start + timedelta(hours=1)
        return MeetingSpec(title=self.title or self.label.title(), start_utc=start, end_utc=end, description=(self.description or "")[:1000] or None,
                           request_id=f"{self.resource_id}{('-' + self.scope) if self.scope else ''}")


def event_tenant(event) -> UUID | None:
    from app.repository.event_repo import event_owner_tenant_id

    return _uuid(event_owner_tenant_id(event))


def _utc(dt, owner) -> datetime | None:
    from app.utils.event_utils import get_event_timezone, resolve_naive_or_aware

    if dt is None:
        return None
    return resolve_naive_or_aware(dt, get_event_timezone(owner)).astimezone(timezone.utc).replace(tzinfo=None)


def event_target(event) -> Target:
    def store(link, provider):
        event.meeting_link = link
        if provider:
            event.meeting_provider = provider

    return Target(kind=KIND_EVENT, owner=event, resource_id=event.id, scope="", tenant_id=event_tenant(event), title=event.title or "Event", description=event.description,
                  start=lambda: _utc(event.start_date, event), end=lambda: _utc(event.end_date, event), virtual=event.delivery_mode in VIRTUAL_MODES,
                  eligible=event.status == "published", stored_link=lambda: _real_link(event.meeting_link), store=store, notify_target={"event_id": event.id}, label="event")


def _find_session(event, session_id: str) -> dict | None:
    return next((s for s in (event.sessions or []) if str(s.get("id")) == str(session_id)), None)


def _session_clock(event, session: dict, which: str) -> datetime | None:
    raw_date, raw_time = session.get("session_date"), session.get("start_time" if which == "start" else "end_time")
    if not raw_date or not raw_time:
        return None
    try:
        day = raw_date if isinstance(raw_date, date) else date.fromisoformat(str(raw_date)[:10])
        hour, minute = (str(raw_time).split(":") + ["0"])[:2]
        return _utc(datetime.combine(day, dtime(int(hour), int(minute))), event)
    except (ValueError, TypeError):
        return None


def event_session_target(event, session_id: str) -> Target:
    session = _find_session(event, session_id)
    if session is None:
        raise _err(404, "Session not found")

    def store(link, provider):
        from sqlalchemy.orm.attributes import flag_modified

        sessions = [dict(s) for s in (event.sessions or [])]
        for item in sessions:
            if str(item.get("id")) == str(session_id):
                item["meeting_link"] = link
        event.sessions = sessions
        flag_modified(event, "sessions")

    current = lambda: _find_session(event, session_id) or {}  # noqa: E731
    return Target(kind=KIND_EVENT_SESSION, owner=event, resource_id=event.id, scope=str(session_id), tenant_id=event_tenant(event), title=session.get("title") or event.title or "Session",
                  description=session.get("description"), start=lambda: _session_clock(event, current(), "start"), end=lambda: _session_clock(event, current(), "end"),
                  virtual=event.delivery_mode in VIRTUAL_MODES, eligible=event.status == "published", stored_link=lambda: _real_link(current().get("meeting_link")), store=store,
                  notify_target={"event_id": event.id}, label="session")


def training_tenant(training) -> UUID | None:
    enterprise = getattr(training, "enterprise", None)
    return _uuid(training.tenant_id or (enterprise.tenant_id if enterprise is not None else None))


def training_session_target(training, live) -> Target:
    def store(link, provider):
        live.meeting_link = link
        if provider:
            live.meeting_provider = TRAINING_PROVIDER_NAMES.get(provider, provider)

    start = lambda: _utc(live.scheduled_at, training)  # noqa: E731

    def end():
        begin = start()
        try:
            return begin + timedelta(minutes=int(live.duration_minutes)) if begin and live.duration_minutes else None
        except (TypeError, ValueError):
            return None

    return Target(kind=KIND_TRAINING_SESSION, owner=live, resource_id=live.id, scope="", tenant_id=training_tenant(training), title=live.title or training.title or "Live session",
                  description=live.description, start=start, end=end, virtual=True, eligible=training.status == "published", stored_link=lambda: _real_link(live.meeting_link),
                  store=store, notify_target={"training_id": training.id}, label="live session", not_virtual_message="A live session is always virtual")


def _row(db: Session, t: Target, create: bool = True) -> MeetingLink | None:
    row = db.query(MeetingLink).filter(MeetingLink.resource_type == t.kind, MeetingLink.resource_id == t.resource_id, MeetingLink.scope == t.scope).first()
    if row is None and create:
        if t.tenant_id is None:
            raise _err(409, "This listing has no business, so it cannot have a meeting")
        row = MeetingLink(tenant_id=t.tenant_id, resource_type=t.kind, resource_id=t.resource_id, scope=t.scope, auto_generate=None, status="none")
        db.add(row)
        db.flush()
    return row


def effective_link(row: MeetingLink | None) -> tuple[str | None, str]:
    """(address, source): a pasted link wins; otherwise the generated one."""
    if row is None:
        return None, "none"
    if row.manual_link:
        return row.manual_link, "manual"
    if row.status == "created" and row.join_url:
        return row.join_url, "generated"
    return None, "none"


def _adopt(row: MeetingLink, t: Target) -> None:
    """An address already on the listing that this module did not create (typed in its form) counts as pasted."""
    current = t.stored_link()
    if current and current != (row.join_url or "") and not row.manual_link:
        row.manual_link = current


def _apply(row: MeetingLink, t: Target, provider: str | None = None) -> None:
    """Keep the listing's own meeting link (read by the existing endpoints and emails) equal to the link customers should use."""
    link, _ = effective_link(row)
    t.store(link, provider)


def _auto(row, prefs) -> bool:
    """The listing's own switch, else the business default."""
    if row is not None and row.auto_generate is not None:
        return bool(row.auto_generate)
    return bool(prefs.auto_generate) if prefs is not None else True


def target_state(db: Session, t: Target) -> dict:
    row = _row(db, t, create=False)
    prefs = merchant_settings(db, t.tenant_id) if t.tenant_id else None
    link, source = effective_link(row)
    if row is None and t.stored_link():
        link, source = t.stored_link(), "manual"
    provider = (row.provider if row else None) or (prefs.default_provider if prefs else None)
    conn = _connection(db, t.tenant_id, provider) if provider and t.tenant_id else None
    return {
        "kind": t.kind, "resource_id": str(t.resource_id), "session_id": t.scope or None, "title": t.title, "virtual": t.virtual,
        "provider": provider, "provider_label": LABELS.get(provider), "auto_generate": _auto(row, prefs),
        "connection_status": "not_connected" if conn is None else conn.status, "manual_link": row.manual_link if row else None,
        "status": "manual" if source == "manual" else (row.status if row else "none"), "meeting_link": link, "link_source": source,
        "passcode": row.passcode if row and source == "generated" else None, "dial_in": row.dial_in if row and source == "generated" else None,
        "last_error": row.last_error if row and row.status == "failed" else None, "attempts": row.attempts if row else 0,
        "needs_attention": bool(row and row.status == "failed" and not row.manual_link), "generated_at": row.generated_at if row else None,
    }


def save_settings(db: Session, user: dict, t: Target, fields_set: set, provider, auto_generate, manual_link) -> dict:
    if not t.virtual:
        raise _err(422, f"{t.not_virtual_message}. Set its delivery mode to online or hybrid first" if t.kind != KIND_TRAINING_SESSION else t.not_virtual_message)
    row = _row(db, t)
    _adopt(row, t)
    actor = _uuid(user.get("id"))
    if "provider" in fields_set:
        if provider is not None:
            _check_provider_key(provider)
        row.provider = provider
    if "auto_generate" in fields_set and auto_generate is not None:
        row.auto_generate = bool(auto_generate)
    changed_link = False
    if "manual_link" in fields_set:
        new = (manual_link or "").strip() or None
        if new and not (new.lower().startswith("https://") or new.lower().startswith("http://")):
            raise _err(422, "The meeting link must start with https://")
        changed_link = (new or None) != (row.manual_link or None)
        row.manual_link = new
    _apply(row, t, row.provider if "provider" in fields_set else None)
    audit(db, "link_settings_changed", tenant_id=t.tenant_id, provider=row.provider, actor_id=actor, resource_type=t.kind, resource_id=t.resource_id,
          details={"auto_generate": row.auto_generate, "manual_link": bool(row.manual_link), "session": t.scope or None})
    db.commit()
    if changed_link:
        _dispatch(_notify_participants, t.kind, t.notify_target, "meeting_link_changed", "The meeting link changed", f"The meeting link for {t.title} was updated. Open it to see the new link.")
    return target_state(db, t)


def _record_failure(db: Session, row: MeetingLink, exc: ProviderError, actor_id, title: str | None) -> None:
    row.status, row.attempts, row.last_error = "failed", (row.attempts or 0) + 1, exc.message[:500]
    audit(db, "generation_failed", tenant_id=row.tenant_id, provider=row.provider, actor_id=actor_id, resource_type=row.resource_type, resource_id=row.resource_id,
          details={"code": exc.code, "attempt": row.attempts, "session": row.scope or None})
    db.commit()
    exhausted = row.attempts >= settings.MEETING_GENERATION_MAX_ATTEMPTS
    if row.attempts == 1 or exhausted:
        recipients = [actor_id] if actor_id else []
        conn = _connection(db, row.tenant_id, row.provider) if row.provider else None
        if conn and conn.connected_by:
            recipients.append(conn.connected_by)
        _dispatch(_notify_users, list(dict.fromkeys(recipients)), "meeting_link_failed", "Meeting link not created",
                  f"The {LABELS.get(row.provider, 'meeting')} link for {title or 'your listing'} could not be created. Open it to retry, or paste a link by hand.",
                  {"resource_type": row.resource_type, "resource_id": str(row.resource_id), "session_id": row.scope or None, "provider": row.provider})


def generate(db: Session, t: Target, actor: dict | None = None, *, force: bool = False, background: bool = False) -> dict:
    """Create the meeting at the provider and put its link on the listing. Never raises for a provider problem: it records the failure.

    force=True is the merchant pressing Generate / Retry: it ignores the automatic switch and the attempt limit."""
    if not t.virtual:
        raise _err(422, t.not_virtual_message)
    row = _row(db, t)
    _adopt(row, t)
    actor_id = _uuid((actor or {}).get("id"))
    if row.manual_link:
        _apply(row, t)
        db.commit()
        return target_state(db, t)                                     # a pasted link wins: nothing to generate
    if row.status == "created" and row.external_id:
        return target_state(db, t)                                     # already created: delete it first to make a new one
    if not force and not _auto(row, merchant_settings(db, t.tenant_id)):
        return target_state(db, t)
    key = row.provider or merchant_settings(db, t.tenant_id).default_provider
    if not key:
        if force:
            raise _err(409, "Choose a meeting provider, or paste a link")
        return target_state(db, t)
    _check_provider_key(key)
    row.provider = key
    if not platform_enabled(db, key):
        if force:
            raise _err(409, f"{LABELS[key]} is switched off for the platform. Choose another provider or paste a link")
        return target_state(db, t)
    conn = _connection(db, t.tenant_id, key)
    if conn is None or conn.status != "connected":
        if force:
            raise _err(409, f"Connect {LABELS[key]} first" if conn is None else f"Reconnect {LABELS[key]} first")
        return target_state(db, t)
    if background and (row.attempts or 0) >= settings.MEETING_GENERATION_MAX_ATTEMPTS:
        return target_state(db, t)
    spec = t.spec()
    provider = get_provider(key)
    result = None
    for attempt in range(2):                                           # one immediate retry for a passing network or provider hiccup
        try:
            result = provider.create(_usable_tokens(db, conn), spec)
            break
        except ProviderAuthError as exc:
            _record_failure(db, row, exc, actor_id, t.title)
            return target_state(db, t)
        except ProviderError as exc:
            if not exc.retryable or attempt == 1:
                _record_failure(db, row, exc, actor_id, t.title)
                return target_state(db, t)
            time.sleep(0.2)
    row.status, row.external_id, row.join_url, row.passcode, row.dial_in = "created", result.external_id, result.join_url, result.passcode, result.dial_in
    row.connection_id, row.attempts, row.last_error, row.generated_at = conn.id, 0, None, _now()
    _apply(row, t, key)
    audit(db, "link_generated", tenant_id=t.tenant_id, provider=key, actor_id=actor_id, resource_type=t.kind, resource_id=t.resource_id, details={"session": t.scope or None})
    db.commit()
    _dispatch(_notify_participants, t.kind, t.notify_target, "meeting_link_added", "Your meeting link is ready", f"The meeting link for {t.title} is ready. Open it to join.")
    return target_state(db, t)


def _provider_call_for(db: Session, row: MeetingLink):
    """(provider, tokens) for the account that created this meeting, or None when it can no longer be reached."""
    conn = db.get(MeetingConnection, row.connection_id) if row.connection_id else None
    if conn is None or conn.status != "connected":
        return None
    try:
        return get_provider(conn.provider), _usable_tokens(db, conn)
    except ProviderError:
        return None


def push_schedule(db: Session, t: Target) -> None:
    """The date, time or title changed: move the meeting at the provider. The link stays the same."""
    row = _row(db, t, create=False)
    if row is None or row.status != "created" or not row.external_id:
        return
    reach = _provider_call_for(db, row)
    if reach is None:
        return
    provider, tokens = reach
    try:
        provider.update(tokens, row.external_id, t.spec())
        audit(db, "meeting_rescheduled", tenant_id=row.tenant_id, provider=row.provider, resource_type=t.kind, resource_id=t.resource_id, details={"session": t.scope or None})
        db.commit()
    except (ProviderError, HTTPException) as exc:
        logger.warning("could not move the %s meeting of %s %s: %s", row.provider, t.kind, t.resource_id, getattr(exc, "message", None) or getattr(exc, "detail", exc))


def remove_generated_row(db: Session, row: MeetingLink, actor: dict | None = None, *, reason: str = "removed", target: Target | None = None) -> None:
    """Delete the generated meeting at the provider and clear the row. A pasted link is left alone."""
    if not row.external_id:
        return
    reach = _provider_call_for(db, row)
    if reach is not None:
        provider, tokens = reach
        try:
            provider.delete(tokens, row.external_id)
        except (ProviderError, HTTPException) as exc:
            logger.warning("could not delete the %s meeting of %s %s: %s", row.provider, row.resource_type, row.resource_id, getattr(exc, "message", None) or exc)
    row.status, row.external_id, row.join_url, row.passcode, row.dial_in, row.attempts, row.last_error = "none", None, None, None, None, 0, None
    if target is not None:
        _apply(row, target)
    audit(db, f"meeting_{reason}", tenant_id=row.tenant_id, provider=row.provider, actor_id=_uuid((actor or {}).get("id")), resource_type=row.resource_type,
          resource_id=row.resource_id, details={"session": row.scope or None})
    db.commit()


def remove_generated_target(db: Session, t: Target, actor: dict | None = None, *, reason: str = "removed") -> dict:
    row = _row(db, t, create=False)
    if row is not None:
        remove_generated_row(db, row, actor, reason=reason, target=t)
    return target_state(db, t)


# ---------------------------------------------------------------- events (kept under their own names)

def _link_row(db: Session, event, create: bool = True) -> MeetingLink | None:
    return _row(db, event_target(event), create)


def state(db: Session, event) -> dict:
    out = target_state(db, event_target(event))
    out["event_id"] = str(event.id)
    return out


def save_event_settings(db: Session, user: dict, event, fields_set: set, provider, auto_generate, manual_link) -> dict:
    save_settings(db, user, event_target(event), fields_set, provider, auto_generate, manual_link)
    return state(db, event)


def generate_for_event(db: Session, event, actor: dict | None = None, *, force: bool = False, background: bool = False) -> dict:
    generate(db, event_target(event), actor, force=force, background=background)
    return state(db, event)


def push_schedule_change(db: Session, event) -> None:
    push_schedule(db, event_target(event))


def remove_generated(db: Session, event, actor: dict | None = None, *, reason: str = "removed") -> dict:
    remove_generated_target(db, event_target(event), actor, reason=reason)
    return state(db, event)


def event_session_states(db: Session, event) -> list[dict]:
    return [target_state(db, event_session_target(event, s["id"])) for s in (event.sessions or []) if s.get("id")]


def _event_rows(db: Session, event) -> list[MeetingLink]:
    return db.query(MeetingLink).filter(MeetingLink.resource_id == event.id, MeetingLink.resource_type.in_((KIND_EVENT, KIND_EVENT_SESSION))).all()


def is_generated_link(db: Session, event) -> bool:
    """True when the event's own link is the one this module generated (so a copy of the event must not reuse it)."""
    try:
        row = _link_row(db, event, create=False)
    except Exception:
        return False
    return bool(row and row.status == "created" and row.join_url and row.join_url == (event.meeting_link or ""))


# ---------------------------------------------------------------- hooks called by the event and training services (never raise)

def _safely(db: Session, what: str, fn) -> None:
    try:
        fn()
    except Exception:
        logger.exception("meeting sync failed: %s", what)
        try:
            db.rollback()
        except Exception:
            pass


def _row_target(db: Session, event, row: MeetingLink) -> Target | None:
    if row.resource_type == KIND_EVENT:
        return event_target(event)
    return event_session_target(event, row.scope) if _find_session(event, row.scope) else None


def on_event_status(db: Session, event, previous: str | None, new_status: str) -> None:
    def run():
        if new_status == "published" and event.delivery_mode in VIRTUAL_MODES:
            generate_for_event(db, event)
            for s in event.sessions or []:                              # a session with its own settings (provider chosen, automatic on) is created too
                row = db.query(MeetingLink).filter(MeetingLink.resource_type == KIND_EVENT_SESSION, MeetingLink.resource_id == event.id,
                                                   MeetingLink.scope == str(s.get("id"))).first()
                if row is not None and row.provider and _auto(row, merchant_settings(db, row.tenant_id)):
                    generate(db, event_session_target(event, s["id"]))
        elif new_status in ("cancelled", "archived", "suspended", "rejected", "draft") and previous == "published":
            reason = "cancelled" if new_status == "cancelled" else "withdrawn"
            for row in _event_rows(db, event):
                remove_generated_row(db, row, reason=reason, target=_row_target(db, event, row))

    _safely(db, f"event {getattr(event, 'id', None)} status", run)


def on_event_updated(db: Session, event, changed: set) -> None:
    def run():
        if changed & {"start_date", "end_date", "time_zone", "title"}:
            push_schedule_change(db, event)
            for row in _event_rows(db, event):
                if row.resource_type == KIND_EVENT_SESSION and _find_session(event, row.scope):
                    push_schedule(db, event_session_target(event, row.scope))
        if "delivery_mode" in changed and event.delivery_mode not in VIRTUAL_MODES:
            for row in _event_rows(db, event):
                remove_generated_row(db, row, reason="withdrawn")
        elif event.status == "published" and event.delivery_mode in VIRTUAL_MODES and changed & {"delivery_mode", "meeting_provider"}:
            generate_for_event(db, event)

    _safely(db, f"event {getattr(event, 'id', None)} update", run)


def on_event_session_changed(db: Session, event, session_id: str, *, deleted: bool = False) -> None:
    """A session of an event was edited (move its meeting) or deleted (delete its meeting)."""
    def run():
        row = db.query(MeetingLink).filter(MeetingLink.resource_type == KIND_EVENT_SESSION, MeetingLink.resource_id == event.id, MeetingLink.scope == str(session_id)).first()
        if row is None:
            return
        if deleted:
            remove_generated_row(db, row, reason="withdrawn")
            db.delete(row)
            db.commit()
        else:
            push_schedule(db, event_session_target(event, session_id))

    _safely(db, f"event {getattr(event, 'id', None)} session {session_id}", run)


def on_event_deleted(db: Session, event) -> None:
    try:
        with db.begin_nested():            # a failure here must not disturb the caller's pending delete
            for row in _event_rows(db, event):
                remove_generated_row(db, row, reason="withdrawn")
    except Exception:
        logger.exception("meeting clean-up after deleting event %s failed", getattr(event, "id", None))


def _training_live_sessions(db: Session, training_id):
    from app.models.training_model import TrainingLiveSession

    return db.query(TrainingLiveSession).filter(TrainingLiveSession.training_id == training_id).all()


def on_training_session_created(db: Session, training, live) -> None:
    def run():
        t = training_session_target(training, live)
        row = _row(db, t) if t.tenant_id else None
        if row is not None:
            _adopt(row, t)
            _apply(row, t)
            db.commit()
        if t.eligible:
            generate(db, t)

    _safely(db, f"training {getattr(training, 'id', None)} session", run)


def on_training_status(db: Session, training, previous: str | None, new_status: str) -> None:
    def run():
        if new_status == "published":
            for live in _training_live_sessions(db, training.id):
                generate(db, training_session_target(training, live))
        elif previous == "published" and new_status in ("unpublished", "suspended", "cancelled", "archived", "draft", "rejected"):
            for live in _training_live_sessions(db, training.id):
                t = training_session_target(training, live)
                row = _row(db, t, create=False)
                if row is not None:
                    remove_generated_row(db, row, reason="cancelled" if new_status == "cancelled" else "withdrawn", target=t)

    _safely(db, f"training {getattr(training, 'id', None)} status", run)


def on_training_deleted(db: Session, training) -> None:
    try:
        with db.begin_nested():
            for live in _training_live_sessions(db, training.id):
                row = _row(db, training_session_target(training, live), create=False)
                if row is not None:
                    remove_generated_row(db, row, reason="withdrawn")
    except Exception:
        logger.exception("meeting clean-up after deleting training %s failed", getattr(training, "id", None))


def placeholders_allowed(provider_key: str | None) -> bool:
    """The older event code invented fake links (random Zoom ids). Once a provider's app is set up, real links replace them."""
    try:
        return not get_provider(provider_key or "").configured()
    except ValueError:
        return True


# ---------------------------------------------------------------- customers

def _join_info(link: str | None, start: datetime | None, end: datetime | None) -> dict:
    opens = start - timedelta(minutes=settings.MEETING_JOIN_OPENS_MINUTES_BEFORE) if start else None
    now = _now()
    return {"starts_at": _iso(start), "ends_at": _iso(end), "join_opens_at": _iso(opens),
            "can_join_now": bool(link and opens and now >= opens and (end is None or now <= end))}


def _customer_item(db: Session, t: Target, fallback_provider: str | None = None) -> dict:
    row = _row(db, t, create=False)
    link, source = effective_link(row)
    if link is None and t.stored_link():
        link, source = t.stored_link(), "manual"
    provider = (row.provider if row else None) or fallback_provider
    return {"session_id": t.scope or None, "title": t.title, "link_ready": link is not None, "meeting_link": link, "provider": provider if provider in LABELS else None,
            "provider_label": LABELS.get(provider), "passcode": row.passcode if row and source == "generated" else None,
            "dial_in": row.dial_in if row and source == "generated" else None, **_join_info(link, t.start(), t.end())}


def customer_join(db: Session, event, email: str | None) -> dict:
    """What a registered customer sees. A pending, cancelled or declined registration, or someone who did not register, gets 404."""
    from app.models.event_aux_models import EventRegistration
    from sqlalchemy import func

    if not email or event.status not in VISIBLE_EVENT_STATUSES:
        raise _err(404, "Meeting not found")
    registered = db.query(EventRegistration.id).filter(
        EventRegistration.event_id == event.id, func.lower(EventRegistration.participant_email) == email.strip().lower(),
        EventRegistration.status.in_(REGISTRATION_STATUSES)).first()
    if not registered:
        raise _err(404, "Meeting not found")
    main = _customer_item(db, event_target(event), event.meeting_provider or None)
    sessions = [item for item in (_customer_item(db, event_session_target(event, s["id"]), event.meeting_provider or None) for s in (event.sessions or []) if s.get("id"))
                if item["link_ready"]]
    return {"event_id": str(event.id), "title": event.title, **{k: v for k, v in main.items() if k not in ("session_id", "title")}, "sessions": sessions}


def customer_training(db: Session, training, email: str | None) -> dict:
    """The live-session links of a training, for a learner with an active enrolment. Anyone else gets 404."""
    from sqlalchemy import func

    from app.models.training_model import TrainingEnrolment

    if not email or training.status not in TRAINING_VISIBLE_STATUSES:
        raise _err(404, "Meeting not found")
    enrolment = db.query(TrainingEnrolment).filter(
        TrainingEnrolment.training_id == training.id, func.lower(TrainingEnrolment.participant_email) == email.strip().lower(),
        TrainingEnrolment.status.in_(TRAINING_ENROLMENT_STATUSES)).first()
    if not enrolment or (enrolment.access_expires_at and enrolment.access_expires_at < _now()):
        raise _err(404, "Meeting not found")
    items = [_customer_item(db, training_session_target(training, live)) for live in sorted(_training_live_sessions(db, training.id), key=lambda s: s.scheduled_at or datetime.max)]
    return {"training_id": str(training.id), "title": training.title, "sessions": items}


# ---------------------------------------------------------------- notifications (best effort, after the change is saved)

def _notify_users(user_ids, category: str, title: str, message: str, metadata: dict, tenant_id=None) -> None:
    ids = [u for u in dict.fromkeys(user_ids or []) if u]
    if not ids:
        return
    from app.db.database import SessionLocal
    from app.services import notification_service

    with SessionLocal() as db:
        notification_service.create_automatic_notification(
            db, title=title, message=message, category=category, user_ids=ids, tenant_id=tenant_id,
            metadata={**{k: v for k, v in metadata.items() if v is not None}, "category": category}, channels=["in_app", "push"])


def _participant_emails(db: Session, target: dict) -> set:
    from sqlalchemy import func

    if "event_id" in target:
        from app.models.event_aux_models import EventRegistration

        rows = db.query(func.lower(EventRegistration.participant_email)).filter(
            EventRegistration.event_id == target["event_id"], EventRegistration.status.in_(REGISTRATION_STATUSES)).all()
    else:
        from app.models.training_model import TrainingEnrolment

        rows = db.query(func.lower(TrainingEnrolment.participant_email)).filter(
            TrainingEnrolment.training_id == target["training_id"], TrainingEnrolment.status.in_(TRAINING_ENROLMENT_STATUSES)).all()
    return {e for (e,) in rows if e}


def _notify_participants(kind: str, target: dict, category: str, title: str, message: str) -> None:
    """Tell the people registered or enrolled. The message carries no link: they open the listing (the link needs a login)."""
    from app.db.database import SessionLocal
    from app.services import notification_service
    from app.services.notification_triggers import _resolve_user_id_from_email

    with SessionLocal() as db:
        user_ids = list(dict.fromkeys(u for u in (_resolve_user_id_from_email(db, e) for e in _participant_emails(db, target)) if u))
        if user_ids:
            notification_service.create_automatic_notification(
                db, title=title, message=message, category=category, user_ids=user_ids,
                metadata={**{k: str(v) for k, v in target.items()}, "category": category}, channels=["in_app", "push"])


def _notify_registrants(event_id, category: str, title: str, message: str) -> None:
    _notify_participants(KIND_EVENT, {"event_id": event_id}, category, title, message)


# ---------------------------------------------------------------- background retry of failed links

def _target_for_row(db: Session, row: MeetingLink) -> Target | None:
    from app.models.event_model import Event
    from app.models.training_model import Training, TrainingLiveSession

    if row.resource_type in (KIND_EVENT, KIND_EVENT_SESSION):
        event = db.get(Event, row.resource_id)
        if event is None or event.is_deleted or event.status != "published":
            return None
        return _row_target(db, event, row)
    live = db.get(TrainingLiveSession, row.resource_id)
    training = db.get(Training, live.training_id) if live is not None else None
    if training is None or training.is_deleted or training.status != "published":
        return None
    return training_session_target(training, live)


def retry_failed(db: Session) -> dict:
    """Generate again for links that failed, up to MEETING_GENERATION_MAX_ATTEMPTS. Safe to run twice."""
    done = {"retried": 0, "created": 0}
    rows = (db.query(MeetingLink).filter(MeetingLink.status == "failed", MeetingLink.attempts < settings.MEETING_GENERATION_MAX_ATTEMPTS,
                                         MeetingLink.manual_link.is_(None), MeetingLink.updated_at < _now() - timedelta(minutes=5))
            .order_by(MeetingLink.updated_at).limit(50).all())
    for row in rows:
        target = _target_for_row(db, row)
        if target is None:
            continue
        done["retried"] += 1
        try:
            if generate(db, target, force=True, background=True)["status"] == "created":
                done["created"] += 1
        except HTTPException:
            continue
    return done


_LOCK_KEY = 7_204_011


def _loop() -> None:
    from app.db.database import SessionLocal

    while True:
        try:
            with SessionLocal() as db:
                locked = True
                if db.bind is not None and db.bind.dialect.name == "postgresql":
                    locked = bool(db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _LOCK_KEY}).scalar())
                if locked:
                    try:
                        retry_failed(db)
                    finally:
                        if db.bind is not None and db.bind.dialect.name == "postgresql":
                            db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_KEY})
                            db.commit()
        except Exception:
            logger.exception("meeting retry round failed")
        time.sleep(settings.MEETING_JOBS_INTERVAL_SECONDS)


def jobs_enabled() -> bool:
    if settings.MEETING_JOBS_ENABLED is not None:
        return bool(settings.MEETING_JOBS_ENABLED)
    return bool(settings.is_production)


def start_scheduler():
    if not jobs_enabled():
        return None
    thread = threading.Thread(target=_loop, name="meeting-retry", daemon=True)
    thread.start()
    return thread
