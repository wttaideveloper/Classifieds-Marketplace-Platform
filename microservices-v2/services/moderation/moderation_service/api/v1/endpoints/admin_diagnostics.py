"""GET /api/v1/admin/notifications/diagnostics (Platform Super Admin), from app/api/v1/endpoints/admin.py.

It shows what notification recipients resolve to and what was recorded for an event or training. In v2 it asks the
owners: events / learning for the record (and its enterprise admins), notifications for the recorded rows.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from mskit import internal
from mskit.auth.dependencies import extract_access_token, get_current_super_admin
from mskit.settings import settings

router = APIRouter(tags=["Admin — Approvals"])


@router.get(
    "/notifications/diagnostics",
    summary="Admin — Why did (or didn't) a workflow notification reach someone",
    description=(
        "Platform Super Admin only. Shows what the notification recipient resolvers see right now: whether the "
        "Invigorate internal API is configured, which Platform Super Admin and owning Enterprise Admin user ids "
        "resolve (and why others were excluded), whether the realtime loop is attached, and — when `event_id` or "
        "`training_id` is given — the notification rows actually recorded for it and their recipients. "
        "Counts and ids only; no emails, names or secrets."
    ),
)
def notification_diagnostics(
    request: Request,
    event_id: UUID | None = Query(None, description="Event whose approval notifications to inspect"),
    training_id: UUID | None = Query(None, description="Training whose approval/enrollment notifications to inspect"),
    _admin: dict = Depends(get_current_super_admin),
):
    from mskit.auth.diagnostics import diagnose_enterprise_admins, diagnose_platform_admins, is_platform_super_admin
    from mskit.remote_records import RemoteRecord

    if not is_platform_super_admin(_admin):
        raise HTTPException(status_code=403, detail="Platform Super Admin only")
    token = extract_access_token(request)
    result = {
        "caller_user_id": str(_admin.get("id")),
        "config": {
            "invigorate_internal_api_configured": settings.invigorate_internal_api_configured,
            "socketio_redis_configured": bool(settings.SOCKETIO_REDIS_URL.strip()),
            # Emits go through Redis to the communication service (no in-process loop in v2): attached == Redis configured
            "realtime_loop_attached": bool(settings.SOCKETIO_REDIS_URL.strip()),
        },
        "platform_admins": diagnose_platform_admins(token),
    }
    for kind, entity_id, owner, path in (("event", event_id, "events", f"/events/{event_id}/snapshot"),
                                         ("training", training_id, "learning", f"/trainings/{training_id}/snapshot")):
        if entity_id is None:
            continue
        r = internal.call(owner, "GET", path)
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail=f"{kind.title()} not found")
        entity = RemoteRecord(kind, owner, r.json())
        recorded = internal.call("notifications", "GET", f"/notifications/recorded/{entity_id}").json()
        result[kind] = {
            "id": str(entity.id),
            "status": entity.status,
            "enterprise_admins": diagnose_enterprise_admins(entity, token),
            "recorded_notifications": recorded,
        }
    return result
