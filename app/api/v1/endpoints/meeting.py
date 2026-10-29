"""Virtual meeting providers (Zoom, Google Meet, Microsoft Teams). See docs/meetings-api.md."""
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import extract_access_token, get_current_user, require_roles
from app.db.database import get_db
from app.models.meeting_model import MeetingAudit
from app.repository.event_repo import build_event_viewer, get_event_by_id, viewer_owns_event
from app.schemas.meeting_schema import (
    ConnectResponse, CustomerMeeting, CustomerTrainingMeetings, DisconnectResponse, EventMeeting, EventMeetingUpdate, MeetingAuditList, MerchantProviderList,
    MerchantSettings, MerchantSettingsUpdate, PlatformProvider, PlatformProviderList, PlatformProviderUpdate, ProviderKey, SessionMeeting, SessionMeetingList,
)
from app.services import meeting_service as svc

router = APIRouter(tags=["Virtual Meetings"])


def _tenant(db: Session, user: dict, request: Request) -> UUID:
    from app.core.auth_context import resolve_auth_tenant_id_with_db

    try:
        raw = resolve_auth_tenant_id_with_db(db, user, access_token=extract_access_token(request))
        return UUID(str(raw))
    except Exception:
        raise HTTPException(status_code=403, detail="Your business could not be determined. Sign in again")


def _merchant_admin(user: dict = Depends(require_roles(["admin"]))) -> dict:
    return user


def _owned_event(db: Session, user: dict, request: Request, event_id: UUID, *, write: bool):
    """The event, for staff of the business that owns it. A Super Admin may read but not change. Anyone else: 404 like the event detail."""
    event = get_event_by_id(db, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    if user.get("role") not in ("admin", "provider", "super_admin"):
        raise HTTPException(status_code=403, detail="Only the business that owns the event can manage its meeting")
    viewer = build_event_viewer(db, user, access_token=extract_access_token(request))
    if not viewer_owns_event(event, viewer):
        raise HTTPException(status_code=404, detail="Event not found")
    if write and user.get("role") == "super_admin":
        raise HTTPException(status_code=403, detail="A Super Admin can see an event's meeting but only the business can change it")
    return event


def _owned_training(db: Session, user: dict, request: Request, training_id: UUID, *, write: bool):
    """The training, for staff of the business that owns it. A Super Admin may read but not change."""
    from app.repository.training_repo import require_training_owner

    if user.get("role") not in ("admin", "provider", "super_admin"):
        raise HTTPException(status_code=403, detail="Only the business that owns the training can manage its meetings")
    training = require_training_owner(db, training_id, user, access_token=extract_access_token(request))
    if write and user.get("role") == "super_admin":
        raise HTTPException(status_code=403, detail="A Super Admin can see a training's meetings but only the business can change them")
    return training


def _live_session(db: Session, training, session_id: UUID):
    from app.models.training_model import TrainingLiveSession

    live = db.query(TrainingLiveSession).filter(TrainingLiveSession.id == session_id, TrainingLiveSession.training_id == training.id).first()
    if live is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return live


# ---------------------------------------------------------------- Super Admin: which providers the platform offers

@router.get("/admin/providers", response_model=PlatformProviderList, summary="Providers and whether the platform offers them (Super Admin)")
def admin_providers(db: Session = Depends(get_db), user: dict = Depends(require_roles(["super_admin"]))):
    return {"items": svc.platform_providers(db)}


@router.put("/admin/providers/{provider}", response_model=PlatformProvider, summary="Switch a provider on or off for the whole platform (Super Admin)",
            description="Off: no business can connect it or create new links with it. Links that already exist keep working. Businesses that had it connected are told.")
def admin_set_provider(payload: PlatformProviderUpdate, provider: ProviderKey = Path(...), db: Session = Depends(get_db), user: dict = Depends(require_roles(["super_admin"]))):
    return svc.set_platform_provider(db, user, provider, payload.enabled)


# ---------------------------------------------------------------- merchant: connections

@router.get("/providers", response_model=MerchantProviderList, summary="My business's providers: Not connected, Connected or Reconnect needed",
            description="Enterprise Admin only. `can_connect` is false when the platform switched the provider off or has not set it up.")
def my_providers(request: Request, db: Session = Depends(get_db), user: dict = Depends(_merchant_admin)):
    return svc.list_providers(db, _tenant(db, user, request))


@router.post("/providers/{provider}/connect", response_model=ConnectResponse, summary="Start connecting a provider (OAuth)",
             description="Returns the provider's sign-in address. Redirect the browser to it. After the merchant agrees, the provider sends them to "
                         "`/api/v1/meetings/oauth/callback/{provider}`, which stores the connection (tokens encrypted) and sends them to "
                         "`{FRONTEND_URL}/settings/meetings?meeting_provider=…&meeting_connected=true|false`.")
def connect_provider(request: Request, provider: ProviderKey = Path(...), db: Session = Depends(get_db), user: dict = Depends(_merchant_admin)):
    return svc.start_connect(db, user, _tenant(db, user, request), provider, str(request.base_url))


@router.get("/oauth/callback/{provider}", include_in_schema=False)
def oauth_callback(request: Request, provider: ProviderKey = Path(...), code: str | None = Query(None), state: str | None = Query(None),
                   error: str | None = Query(None), db: Session = Depends(get_db)):
    """Called by the provider's redirect, not by the app. The signed `state` says which business and user started it."""
    result = svc.finish_connect(db, provider, code, state, error, str(request.base_url))
    query = {"meeting_provider": provider, "meeting_connected": "true" if result["ok"] else "false"}
    if not result["ok"]:
        query["reason"] = result["reason"] or "failed"
    return RedirectResponse(f"{settings.FRONTEND_URL.rstrip('/')}/settings/meetings?{urlencode(query)}", status_code=302)


@router.delete("/providers/{provider}", response_model=DisconnectResponse, summary="Disconnect a provider",
               description="Deletes the stored tokens and revokes access at the provider where it supports that. Links already created keep their address.")
def disconnect_provider(request: Request, provider: ProviderKey = Path(...), db: Session = Depends(get_db), user: dict = Depends(_merchant_admin)):
    return svc.disconnect(db, user, _tenant(db, user, request), provider)


@router.get("/settings", response_model=MerchantSettings, summary="My business's default provider and automatic-link switch")
def get_settings(request: Request, db: Session = Depends(get_db), user: dict = Depends(_merchant_admin)):
    row = svc.merchant_settings(db, _tenant(db, user, request))
    return {"default_provider": row.default_provider, "auto_generate": bool(row.auto_generate)}


@router.put("/settings", response_model=MerchantSettings, summary="Choose the default provider and the automatic-link switch",
            description="The default provider must be connected. Send only the fields you change.")
def put_settings(payload: MerchantSettingsUpdate, request: Request, db: Session = Depends(get_db), user: dict = Depends(_merchant_admin)):
    return svc.update_merchant_settings(db, user, _tenant(db, user, request), payload.default_provider, payload.auto_generate, payload.model_fields_set)


@router.get("/audit", response_model=MeetingAuditList, summary="Who connected, disconnected or changed a link, and when",
            description="Enterprise Admin: their own business. Super Admin: everything, optionally for one `tenant_id`. Newest first. Never contains a token.")
def audit_list(request: Request, tenant_id: UUID | None = Query(None), limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db),
               user: dict = Depends(require_roles(["admin", "super_admin"]))):
    query = db.query(MeetingAudit)
    if user.get("role") == "super_admin":
        if tenant_id:
            query = query.filter(MeetingAudit.tenant_id == tenant_id)
    else:
        query = query.filter(MeetingAudit.tenant_id == _tenant(db, user, request))
    rows = query.order_by(MeetingAudit.created_at.desc()).limit(limit).all()
    return {"total": query.count(), "items": [{"id": str(r.id), "action": r.action, "provider": r.provider, "actor_id": str(r.actor_id) if r.actor_id else None,
                                               "resource_type": r.resource_type, "resource_id": str(r.resource_id) if r.resource_id else None,
                                               "details": r.details, "created_at": r.created_at} for r in rows]}


# ---------------------------------------------------------------- merchant: the meeting of one event

@router.get("/events/{event_id}", response_model=EventMeeting, summary="An event's meeting settings and link (the business)",
            description="Enterprise Admin or Provider of the business that owns the event (a Super Admin may read). `status: failed` means show \"Meeting link not created\" with Retry.")
def event_meeting(request: Request, event_id: UUID = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.state(db, _owned_event(db, user, request, event_id, write=False))


@router.put("/events/{event_id}", response_model=EventMeeting, summary="Choose the provider, switch automatic links on or off, or paste a link",
            description="Send only the fields you change. `manual_link` takes priority over a generated link; send null or \"\" to remove it. "
                        "An in-person event has no meeting settings (422). Customers registered for the event are told when the link changes.")
def put_event_meeting(payload: EventMeetingUpdate, request: Request, event_id: UUID = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    event = _owned_event(db, user, request, event_id, write=True)
    return svc.save_event_settings(db, user, event, payload.model_fields_set, payload.provider, payload.auto_generate, payload.manual_link)


@router.post("/events/{event_id}/generate", response_model=EventMeeting, summary="Create the meeting link now (also the Retry button)",
             description="Creates the meeting at the chosen provider. 409 with a plain reason when no provider is chosen, it is switched off, or it is not connected (or needs reconnecting). "
                         "A provider error does not fail the call: the answer has `status: failed` and `last_error`. Does nothing when a pasted link is in use.")
def generate_event_meeting(request: Request, event_id: UUID = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    event = _owned_event(db, user, request, event_id, write=True)
    return svc.generate_for_event(db, event, user, force=True)


@router.delete("/events/{event_id}/generated", response_model=EventMeeting, summary="Delete the generated meeting",
               description="Deletes the meeting at the provider and removes the generated link. A pasted link is left alone.")
def delete_event_meeting(request: Request, event_id: UUID = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    event = _owned_event(db, user, request, event_id, write=True)
    return svc.remove_generated(db, event, user, reason="removed")


# ---------------------------------------------------------------- merchant: one session of an event

@router.get("/events/{event_id}/sessions", response_model=SessionMeetingList, summary="The meeting of every session of an event (the business)",
            description="One entry per session of the event. A session has its own meeting only when the merchant chose a provider or pasted a link for it; the event's own meeting is separate.")
def event_session_meetings(request: Request, event_id: UUID = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return {"items": svc.event_session_states(db, _owned_event(db, user, request, event_id, write=False))}


@router.get("/events/{event_id}/sessions/{session_id}", response_model=SessionMeeting, summary="One session's meeting settings and link (the business)")
def event_session_meeting(request: Request, event_id: UUID = Path(...), session_id: str = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.target_state(db, svc.event_session_target(_owned_event(db, user, request, event_id, write=False), session_id))


@router.put("/events/{event_id}/sessions/{session_id}", response_model=SessionMeeting, summary="Choose the provider, switch automatic links, or paste a link for one session",
            description="Same fields and rules as the event's own meeting. A session's link is kept on the session (`sessions[].meeting_link`).")
def put_event_session_meeting(payload: EventMeetingUpdate, request: Request, event_id: UUID = Path(...), session_id: str = Path(...), db: Session = Depends(get_db),
                              user: dict = Depends(get_current_user)):
    event = _owned_event(db, user, request, event_id, write=True)
    return svc.save_settings(db, user, svc.event_session_target(event, session_id), payload.model_fields_set, payload.provider, payload.auto_generate, payload.manual_link)


@router.post("/events/{event_id}/sessions/{session_id}/generate", response_model=SessionMeeting, summary="Create one session's meeting now (also Retry)",
             description="Needs the session's date and start time (422 otherwise). The end time defaults to one hour after the start.")
def generate_event_session_meeting(request: Request, event_id: UUID = Path(...), session_id: str = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    event = _owned_event(db, user, request, event_id, write=True)
    return svc.generate(db, svc.event_session_target(event, session_id), user, force=True)


@router.delete("/events/{event_id}/sessions/{session_id}/generated", response_model=SessionMeeting, summary="Delete one session's generated meeting")
def delete_event_session_meeting(request: Request, event_id: UUID = Path(...), session_id: str = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    event = _owned_event(db, user, request, event_id, write=True)
    return svc.remove_generated_target(db, svc.event_session_target(event, session_id), user)


# ---------------------------------------------------------------- merchant: live sessions of a training

@router.get("/trainings/{training_id}/sessions", response_model=SessionMeetingList, summary="The meeting of every live session of a training (the business)")
def training_session_meetings(request: Request, training_id: UUID = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    training = _owned_training(db, user, request, training_id, write=False)
    return {"items": [svc.target_state(db, svc.training_session_target(training, live)) for live in sorted(svc._training_live_sessions(db, training.id), key=lambda s: s.scheduled_at)]}


@router.get("/trainings/{training_id}/sessions/{session_id}", response_model=SessionMeeting, summary="One live session's meeting settings and link (the business)")
def training_session_meeting(request: Request, training_id: UUID = Path(...), session_id: UUID = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    training = _owned_training(db, user, request, training_id, write=False)
    return svc.target_state(db, svc.training_session_target(training, _live_session(db, training, session_id)))


@router.put("/trainings/{training_id}/sessions/{session_id}", response_model=SessionMeeting, summary="Choose the provider, switch automatic links, or paste a link for a live session",
            description="Same fields and rules as an event's meeting. The link is also written to the live session's `meeting_link`, which `GET /trainings/{id}/meeting-link` already serves to enrolled learners.")
def put_training_session_meeting(payload: EventMeetingUpdate, request: Request, training_id: UUID = Path(...), session_id: UUID = Path(...), db: Session = Depends(get_db),
                                 user: dict = Depends(get_current_user)):
    training = _owned_training(db, user, request, training_id, write=True)
    return svc.save_settings(db, user, svc.training_session_target(training, _live_session(db, training, session_id)), payload.model_fields_set,
                             payload.provider, payload.auto_generate, payload.manual_link)


@router.post("/trainings/{training_id}/sessions/{session_id}/generate", response_model=SessionMeeting, summary="Create a live session's meeting now (also Retry)")
def generate_training_session_meeting(request: Request, training_id: UUID = Path(...), session_id: UUID = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    training = _owned_training(db, user, request, training_id, write=True)
    return svc.generate(db, svc.training_session_target(training, _live_session(db, training, session_id)), user, force=True)


@router.delete("/trainings/{training_id}/sessions/{session_id}/generated", response_model=SessionMeeting, summary="Delete a live session's generated meeting")
def delete_training_session_meeting(request: Request, training_id: UUID = Path(...), session_id: UUID = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    training = _owned_training(db, user, request, training_id, write=True)
    return svc.remove_generated_target(db, svc.training_session_target(training, _live_session(db, training, session_id)), user)


# ---------------------------------------------------------------- customer

@router.get("/my/events/{event_id}", response_model=CustomerMeeting, summary="My meeting for an event I registered for",
            description="Only for the caller's own confirmed or attended registration of a published or completed event; anyone else gets 404. "
                        "The link is returned as soon as it exists so it can be copied; `can_join_now` says whether the Join button is active (from 15 minutes before the start until the end).")
def my_event_meeting(event_id: UUID = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    event = get_event_by_id(db, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return svc.customer_join(db, event, user.get("email"))


@router.get("/my/trainings/{training_id}", response_model=CustomerTrainingMeetings, summary="The live-session links of a training I am enrolled in",
            description="Only for a learner with an active, unexpired enrolment in a published or completed training; anyone else gets 404. One entry per live session, soonest first. "
                        "The link is returned as soon as it exists; `can_join_now` is true from 15 minutes before the session until its end.")
def my_training_meetings(training_id: UUID = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    from app.repository.training_repo import get_training_by_id

    training = get_training_by_id(db, training_id)
    if training is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return svc.customer_training(db, training, user.get("email"))
