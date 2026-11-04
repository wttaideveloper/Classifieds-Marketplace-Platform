"""The HTTP API. Callers are other services, each with its own key (header `X-API-Key`).

Producers send events and may read/ack a person's notifications on their behalf (the app talks to its own backend, which talks to this
service). The admin key manages event types, nudge rules and looks at deliveries.
"""
import hmac
from math import ceil

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app import pipeline
from app.config import get_settings
from app.db import get_db
from app.models import Delivery, Device, EventType, Notification, NudgeRule, Preference
from app.schemas import (
    DeliveryOut, DeviceIn, EventIn, EventOut, EventTypeIn, EventTypeOut, MarkRead, NotificationOut, NotificationPage, NudgeRuleIn, NudgeRuleOut,
    PreferenceIn, PreferencesOut,
)

router = APIRouter(prefix="/v1")


def _match(key: str | None, candidates) -> str | None:
    if not key:
        return None
    found = None
    for candidate, name in candidates:
        if hmac.compare_digest(key.encode(), candidate.encode()):
            found = name
    return found


def producer(x_api_key: str | None = Header(None, alias="X-API-Key")) -> str:
    settings = get_settings()
    name = _match(x_api_key, settings.producers.items())
    if name is None and settings.ADMIN_KEY and x_api_key and hmac.compare_digest(x_api_key.encode(), settings.ADMIN_KEY.encode()):
        name = "admin"
    if name is None:
        raise HTTPException(401, "A valid X-API-Key is needed")
    return name


def admin(x_api_key: str | None = Header(None, alias="X-API-Key")) -> str:
    settings = get_settings()
    if not x_api_key:
        raise HTTPException(401, "A valid X-API-Key is needed")
    if not settings.ADMIN_KEY or not hmac.compare_digest(x_api_key.encode(), settings.ADMIN_KEY.encode()):
        raise HTTPException(403, "This needs the admin key")
    return "admin"


# ---------------------------------------------------------------- sending

@router.post("/events", response_model=EventOut, status_code=202, summary="Send an event to one or more people",
             description="The event type decides the wording, channels and priority. Every recipient gets a notification in their inbox; push and email go through a queue "
                         "with retries and respect each person's preferences (a `critical` event ignores them). The same `idempotency_key` for the same person is sent once.")
def send_event(payload: EventIn, db: Session = Depends(get_db), source: str = Depends(producer)):
    try:
        results = pipeline.ingest(
            db, source=source, event=payload.event, recipients=[r.model_dump() for r in payload.recipients], data=payload.data,
            tenant_id=payload.tenant_id, idempotency_key=payload.idempotency_key, channels=payload.channels, title=payload.title, body=payload.body,
        )
    except pipeline.UnknownEvent:
        raise HTTPException(422, f'"{payload.event}" is not a registered event type. Register it with PUT /v1/admin/event-types/{{event}} or send a `title`')
    return {"results": results}


# ---------------------------------------------------------------- a person's notifications

@router.get("/users/{user_id}/notifications", response_model=NotificationPage, summary="A person's inbox, newest first")
def inbox(user_id: str, unread_only: bool = Query(False), category: str | None = Query(None), tenant_id: str | None = Query(None),
          page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db: Session = Depends(get_db), _: str = Depends(producer)):
    query = db.query(Notification).filter(Notification.user_id == user_id)
    if unread_only:
        query = query.filter(Notification.read_at.is_(None))
    if category:
        query = query.filter(Notification.category == category)
    if tenant_id:
        query = query.filter(Notification.tenant_id == tenant_id)
    total = query.count()
    rows = query.order_by(Notification.created_at.desc(), Notification.id).offset((page - 1) * page_size).limit(page_size).all()
    unread = db.query(Notification).filter(Notification.user_id == user_id, Notification.read_at.is_(None)).count()
    return {"items": rows, "unread_count": unread, "pagination": {"total": total, "page": page, "page_size": page_size, "total_pages": ceil(total / page_size)}}


@router.get("/users/{user_id}/notifications/unread-count", summary="How many unread")
def unread_count(user_id: str, db: Session = Depends(get_db), _: str = Depends(producer)):
    return {"unread_count": db.query(Notification).filter(Notification.user_id == user_id, Notification.read_at.is_(None)).count()}


@router.post("/users/{user_id}/notifications/read", summary="Mark notifications as read",
             description="`{\"ids\": [...]}` or `{\"all\": true}`. Marking one that is already read changes nothing.")
def mark_read(user_id: str, payload: MarkRead, db: Session = Depends(get_db), _: str = Depends(producer)):
    from datetime import datetime

    if not payload.all and not payload.ids:
        raise HTTPException(422, "Send ids, or all=true")
    query = db.query(Notification).filter(Notification.user_id == user_id, Notification.read_at.is_(None))
    if not payload.all:
        query = query.filter(Notification.id.in_(payload.ids))
    count = query.update({Notification.read_at: datetime.utcnow()}, synchronize_session=False)
    db.commit()
    return {"marked": count}


# ---------------------------------------------------------------- devices and preferences

@router.post("/users/{user_id}/devices", status_code=201, summary="Register a push token",
             description="Registering a token that exists moves it to this person (a phone changes hands).")
def add_device(user_id: str, payload: DeviceIn, db: Session = Depends(get_db), _: str = Depends(producer)):
    row = db.query(Device).filter(Device.token == payload.token).first()
    if row is None:
        db.add(Device(user_id=user_id, token=payload.token, platform=payload.platform))
    else:
        row.user_id, row.platform = user_id, payload.platform
    db.commit()
    return {"registered": True}


@router.delete("/users/{user_id}/devices", summary="Remove a push token (sign-out)")
def remove_device(user_id: str, token: str = Query(...), db: Session = Depends(get_db), _: str = Depends(producer)):
    count = db.query(Device).filter(Device.user_id == user_id, Device.token == token).delete()
    db.commit()
    return {"removed": count}


@router.get("/users/{user_id}/preferences", response_model=PreferencesOut, summary="What the person switched off or on",
            description="Only choices that were made are listed. Anything not listed is allowed.")
def get_preferences(user_id: str, db: Session = Depends(get_db), _: str = Depends(producer)):
    return {"items": [PreferenceIn(category=p.category, channel=p.channel, enabled=p.enabled) for p in db.query(Preference).filter(Preference.user_id == user_id).all()]}


@router.put("/users/{user_id}/preferences", response_model=PreferencesOut, summary="Switch a category on or off for a channel")
def set_preference(user_id: str, payload: PreferenceIn, db: Session = Depends(get_db), _: str = Depends(producer)):
    row = db.query(Preference).filter_by(user_id=user_id, category=payload.category, channel=payload.channel).first()
    if row is None:
        db.add(Preference(user_id=user_id, category=payload.category, channel=payload.channel, enabled=payload.enabled))
    else:
        row.enabled = payload.enabled
    db.commit()
    return get_preferences(user_id, db, "")


# ---------------------------------------------------------------- admin: event types, nudges, deliveries

@router.get("/admin/event-types", response_model=list[EventTypeOut], summary="Registered event types")
def list_event_types(db: Session = Depends(get_db), _: str = Depends(admin)):
    return db.query(EventType).order_by(EventType.event).all()


@router.put("/admin/event-types/{event}", response_model=EventTypeOut, summary="Create or change an event type")
def put_event_type(event: str, payload: EventTypeIn, db: Session = Depends(get_db), _: str = Depends(admin)):
    if payload.event != event:
        raise HTTPException(422, "The event in the path and in the body must match")
    row = db.get(EventType, event)
    if row is None:
        row = EventType(event=event)
        db.add(row)
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    db.commit()
    return row


@router.get("/admin/nudge-rules", response_model=list[NudgeRuleOut], summary="Nudge rules")
def list_rules(db: Session = Depends(get_db), _: str = Depends(admin)):
    return db.query(NudgeRule).order_by(NudgeRule.name).all()


@router.post("/admin/nudge-rules", response_model=NudgeRuleOut, status_code=201, summary="Add a nudge rule",
             description="When `trigger_event` arrives for a person (with `data[subject_field]`), a nudge is scheduled `delay_minutes` later, unless `cancel_event` "
                         "arrives for the same person and subject first. `max_nudges` and `repeat_minutes` repeat it.")
def add_rule(payload: NudgeRuleIn, db: Session = Depends(get_db), _: str = Depends(admin)):
    if payload.nudge_event == payload.trigger_event:
        raise HTTPException(422, "The nudge cannot be the same event that starts it")
    if db.query(NudgeRule).filter(NudgeRule.name == payload.name).first():
        raise HTTPException(409, "A rule with this name exists")
    if db.get(EventType, payload.nudge_event) is None:
        raise HTTPException(422, f'Register the event type "{payload.nudge_event}" first: it is the wording of the reminder')
    if payload.max_nudges > 1 and not payload.repeat_minutes:
        payload = payload.model_copy(update={"repeat_minutes": payload.delay_minutes})
    row = NudgeRule(**payload.model_dump())
    db.add(row)
    db.commit()
    return row


@router.patch("/admin/nudge-rules/{rule_id}/active", response_model=NudgeRuleOut, summary="Switch a rule on or off")
def set_rule_active(rule_id: str, active: bool = Query(...), db: Session = Depends(get_db), _: str = Depends(admin)):
    row = db.get(NudgeRule, rule_id)
    if row is None:
        raise HTTPException(404, "Rule not found")
    row.active = active
    db.commit()
    return row


@router.get("/admin/deliveries", response_model=list[DeliveryOut], summary="Push and email deliveries",
            description="Filter by `status` (pending, sent, failed, skipped). Failed ones gave up after MAX_ATTEMPTS.")
def list_deliveries(status: str | None = Query(None), limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db), _: str = Depends(admin)):
    query = db.query(Delivery)
    if status:
        query = query.filter(Delivery.status == status)
    return query.order_by(Delivery.created_at.desc()).limit(limit).all()
