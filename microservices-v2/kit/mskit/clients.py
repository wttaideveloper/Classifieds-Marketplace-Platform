"""Typed clients for the internal APIs. Signatures mirror the monolith functions they replace, so the
copied domain code changes only its import line.

Each function documents the monolith behaviour it reproduces.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from mskit.internal import call


def _commit_caller(db) -> None:
    """The monolith's notification repository committed the CALLER's session before writing the
    notification. Commit here so the caller's pending changes become visible exactly when they did."""
    if db is not None:
        db.commit()


# ---------------------------------------------------------------- notifications service

def create_automatic_notification(db, *, title: str, message: str, category: str, user_ids: list,
                                  tenant_id=None, metadata: dict | None = None, channels: list[str] | None = None):
    """app.services.notification_service.create_automatic_notification over HTTP.
    Returns an object with ``.id`` (or None when there are no recipients), like the monolith."""
    if not user_ids:
        return None
    _commit_caller(db)
    r = call("notifications", "POST", "/notifications/automatic", json={
        "title": title, "message": message, "category": category,
        "user_ids": [str(u) for u in user_ids],
        "tenant_id": str(tenant_id) if tenant_id else None,
        "metadata": metadata or {}, "channels": channels,
    })
    data = r.json()
    return SimpleNamespace(id=UUID(data["id"]), status=data.get("status"))


def claim_dedupe_key(db, dedupe_key: str) -> bool:
    """app.services.notification_idempotency.claim over HTTP (True = first claim, send it)."""
    _commit_caller(db)
    return bool(call("notifications", "POST", "/notification-dedupe/claim", json={"key": dedupe_key}).json()["claimed"])


def release_dedupe_key(db, dedupe_key: str) -> None:
    """app.services.notification_idempotency.release over HTTP."""
    _commit_caller(db)
    call("notifications", "POST", "/notification-dedupe/release", json={"key": dedupe_key})


# ---------------------------------------------------------------- communication service

def delivery_profile(user_id) -> SimpleNamespace:
    """Push preferences + active device tokens of a user (communication owns both tables).
    Like chat_repo.get_notification_preferences, the communication service creates the default
    preferences row when the user has none."""
    data = call("communication", "GET", f"/users/{user_id}/delivery-profile").json()
    return SimpleNamespace(push_enabled=data["push_enabled"], tokens=list(data["tokens"]), email=data.get("email"))


def _raise_passthrough(response) -> None:
    """Re-raise an owning service's HTTP error exactly as the in-process call raised it."""
    from fastapi import HTTPException

    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text
        raise HTTPException(status_code=response.status_code, detail=detail)


# ---------------------------------------------------------------- learning service

def training_upload_field(current_user: dict, field_key, training_id):
    """training_form_media.resolve_upload_field + policy_cap. Returns (field | None, cap | None);
    raises the same HTTPException the learning code raises (unknown training, not owner, ...)."""
    r = call("learning", "POST", "/training-media/upload-field", expect=range(200, 600), json={
        "user": current_user, "field_key": field_key, "training_id": str(training_id) if training_id else None,
    })
    _raise_passthrough(r)
    data = r.json()
    return data.get("field"), data.get("cap")


def validate_training_media(field: dict, data: bytes, claimed_mime, purpose) -> None:
    """training_form_media.validate_media_bytes, evaluated by learning (the owner of the rules)."""
    import json as _json

    from mskit.internal import _client  # multipart needs the raw client
    import os

    headers = {"X-Internal-Api-Key": os.environ.get("INTERNAL_API_KEY", "")}
    r = _client("learning").post(
        "/internal/v1/training-media/validate",
        headers=headers,
        data={"field": _json.dumps(field), "claimed_mime": claimed_mime or "", "purpose": purpose or ""},
        files={"file": ("upload", data, "application/octet-stream")},
        timeout=120.0,
    )
    _raise_passthrough(r)


# ---------------------------------------------------------------- catalog service

def enterprise_for_tenant(tenant_id, enterprise_id=None) -> SimpleNamespace | None:
    """Enterprises of a tenant (not deleted): the given one, else the oldest. None when there is none.
    Replaces `db.query(Enterprise).filter(tenant_id == ..., is_deleted false)[.filter(id ==)].order_by(created_at).first()`."""
    params = {"enterprise_id": str(enterprise_id)} if enterprise_id else None
    r = call("catalog", "GET", f"/tenants/{tenant_id}/enterprise", params=params)
    if r.status_code == 404:
        return None
    return SimpleNamespace(**{**r.json(), "id": UUID(r.json()["id"]), "tenant_id": UUID(r.json()["tenant_id"]) if r.json().get("tenant_id") else None})


def business_names_by_tenant(tenant_ids) -> dict:
    """{tenant_id: business_short_name or business_legal_name} of each tenant's oldest live enterprise."""
    if not tenant_ids:
        return {}
    r = call("catalog", "POST", "/enterprises/business-names", json={"tenant_ids": [str(t) for t in tenant_ids]})
    return {UUID(k): v for k, v in r.json().items()}


# ---------------------------------------------------------------- moderation service

def record_review_moderation(db, **entry) -> None:
    """review_audit.record: one row of the review moderation history (moderation owns it).
    Monolith: added inside the caller's transaction, swallowing errors. Here: sent after the caller
    commits (dropped on rollback); a failure is logged, never raised - same as the monolith."""
    from mskit.after_commit import defer

    payload = {k: (str(v) if v is not None and not isinstance(v, (str, int, float, bool, dict, list)) else v) for k, v in entry.items()}

    def send():
        call("moderation", "POST", "/review-log", json=payload)

    defer(db, send)


# ---------------------------------------------------------------- commerce service

def user_has_confirmed_purchase(user_id, product_id) -> bool:
    """Was the product in one of the user's confirmed orders? (verified-purchase badge on reviews)."""
    if not user_id or not product_id:
        return False
    r = call("commerce", "GET", f"/users/{user_id}/confirmed-purchases/{product_id}")
    return bool(r.json().get("purchased")) if r.status_code == 200 else False


def record_notification(db, **fields):
    """notification_repo.create_notification for an audit/trace row (notification_triggers step 4).
    Monolith: written and committed in its own session; same here (notifications service)."""
    payload = {k: (str(v) if isinstance(v, UUID) else v) for k, v in fields.items()}
    return call("notifications", "POST", "/notifications/records", json=payload).json()


def mark_message_notifications_read(db, user_id, message_id) -> None:
    """event_discussion_notifications.mark_read: the reader's unread event_discussion_* notifications that
    mention the message become read. Monolith: inside the caller's transaction (return value unused);
    here: after the caller commits, dropped on rollback."""
    from mskit.after_commit import defer

    def send():
        call("notifications", "POST", "/notifications/mark-read-by-reference",
             json={"user_id": str(user_id), "category_like": "event_discussion_%", "reference": str(message_id)})

    defer(db, send)
    return 0


# ---------------------------------------------------------------- meetings service
# The monolith's meeting_service received the Event / Training ORM object and wrote generated links straight
# into it (event.meeting_link, event.meeting_provider, a session's meeting_link). Now the owner sends a snapshot,
# meetings does the provider work and answers with the writes; the owner applies them to its own row, so the
# response of the owner's request still carries the new link. Failures are logged and never raised (the
# monolith wrapped these hooks the same way).

import logging as _logging  # noqa: E402

_mlog = _logging.getLogger("mskit.clients.meetings")


def snapshot_of(record, **extra) -> dict:
    from mskit.replication import row_of

    data = row_of(record)
    enterprise = getattr(record, "enterprise", None)
    data["_enterprise_tenant_id"] = {"__uuid__": str(enterprise.tenant_id)} if enterprise is not None and enterprise.tenant_id else None
    data.update(extra)
    return data


def apply_meeting_writes(record, writes: list[dict]) -> bool:
    """Store links meetings generated: scope "" = the record itself, else the session with that id."""
    from sqlalchemy.orm.attributes import flag_modified

    changed = False
    for w in writes or []:
        if not w.get("scope"):
            record.meeting_link = w.get("link")
            if w.get("provider"):
                record.meeting_provider = w["provider"]
            changed = True
        else:
            sessions = [dict(s) for s in (record.sessions or [])]
            for item in sessions:
                if str(item.get("id")) == str(w["scope"]):
                    item["meeting_link"] = w.get("link")
                    changed = True
            record.sessions = sessions
            flag_modified(record, "sessions")
    return changed


def _meeting_hook(path: str, db, record, *, commit: bool, **payload) -> None:
    try:
        r = call("meetings", "POST", path, json={"record": snapshot_of(record), **payload}, timeout=30.0)
        if apply_meeting_writes(record, r.json().get("writes")) and commit and db is not None:
            db.commit()
            db.refresh(record)
    except Exception:  # noqa: BLE001
        _mlog.exception("meetings hook %s failed for %s", path, getattr(record, "id", None))


def meetings_on_event_updated(db, event, changed: set) -> None:
    _meeting_hook("/hooks/events/updated", db, event, commit=True, changed=sorted(changed))


def meetings_on_event_status(db, event, previous, new_status) -> None:
    _meeting_hook("/hooks/events/status", db, event, commit=True, previous=previous, new_status=new_status)


def meetings_on_event_session_changed(db, event, session_id, *, deleted: bool = False) -> None:
    _meeting_hook("/hooks/events/session", db, event, commit=True, session_id=str(session_id), deleted=deleted)


def meetings_on_event_deleted(db, event) -> None:
    # before the caller's delete commits: writes (if any) ride on the caller's commit
    _meeting_hook("/hooks/events/deleted", db, event, commit=False)


def meetings_placeholders_allowed(provider_key) -> bool:
    """meeting_service.placeholders_allowed: may an invented placeholder link be used for this provider?"""
    try:
        return bool(call("meetings", "GET", "/providers/placeholders-allowed", params={"provider": provider_key or ""}).json()["allowed"])
    except Exception:  # noqa: BLE001
        _mlog.exception("meetings placeholders_allowed failed; treating as not allowed")
        return False


def meetings_is_generated_link(db, record) -> bool:
    """meeting_service.is_generated_link: is the record's link one meetings generated (and still owns)?"""
    try:
        return bool(call("meetings", "POST", "/records/is-generated-link", json={"record": snapshot_of(record)}).json()["generated"])
    except Exception:  # noqa: BLE001
        _mlog.exception("meetings is_generated_link failed; treating as not generated")
        return False


def meetings_call(path: str, payload: dict, timeout: float = 30.0) -> list[dict]:
    """POST a hook to meetings and return the link writes it asks the caller to store ([] on failure, logged)."""
    try:
        return call("meetings", "POST", path, json=payload, timeout=timeout).json().get("writes") or []
    except Exception:  # noqa: BLE001
        _mlog.exception("meetings hook %s failed", path)
        return []



# ---------------------------------------------------------------- catalog stock (design D1)

def reserve_stock(key: str, lines: list) -> dict:
    """Validate, price and take stock for [(product_id, quantity)] in catalog; HTTP errors re-raised unchanged."""
    r = call("catalog", "POST", "/stock/reservations", expect=range(200, 600), timeout=30.0,
             json={"key": key, "items": [{"product_id": str(pid), "quantity": int(q)} for pid, q in lines]})
    _raise_passthrough(r)
    return r.json()


def release_stock_reservation(reservation_id: str) -> None:
    try:
        call("catalog", "DELETE", f"/stock/reservations/{reservation_id}")
    except Exception:  # noqa: BLE001
        _mlog.exception("could not release stock reservation %s; it expires on its own", reservation_id)
