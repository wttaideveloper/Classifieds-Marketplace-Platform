"""Internal API of the communication service (/internal/v1, shared key, never routed by the gateway).

* GET  /users/{id}/delivery-profile   push preferences (created on first use, as before) + active device tokens
* POST /replica/{topic}               pushed by catalog, events and learning (read-only copies for the group chats)
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from communication_service.db.database import get_db
from communication_service.startup import REPLICAS
from mskit.app import require_internal_key

router = APIRouter(dependencies=[Depends(require_internal_key)], tags=["internal"])


@router.get("/users/{user_id}/delivery-profile")
def delivery_profile(user_id: UUID, db: Session = Depends(get_db)):
    from communication_service.repository import chat_repo

    prefs = chat_repo.get_notification_preferences(db, user_id)          # creates the default row, as before
    tokens = [d.token for d in chat_repo.get_active_device_tokens(db, user_id) if d.is_active]
    return {"push_enabled": bool(prefs.push_enabled), "tokens": tokens, "email": None}


replica_router = APIRouter(dependencies=[Depends(require_internal_key)], tags=["internal"])


@replica_router.post("/replica/{topic}")
def receive(topic: str, batch: dict):
    """Push from an owner: the subscriber holding that topic applies it (others ignore it)."""
    for subscriber in REPLICAS:
        if topic in subscriber.topics:
            return subscriber.apply(topic, batch.get("upsert") or [], batch.get("delete") or [], full=bool(batch.get("full")))
    return {"ignored": topic}


routers = [router, replica_router]
