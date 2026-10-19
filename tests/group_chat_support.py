"""Shared setup for the group chat tests: an in-memory database with the chat and group tables, the real FastAPI
routes, and everything that leaves the process (realtime events, notifications, push) captured instead of sent."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import conversation, conversation_messages, group_chat, message, typing_indicator
from app.core.config import settings
from app.core.dependencies import get_current_user
from app.db.database import Base, get_db
from app.models.chat_model import (
    ChatAttachment, ChatNotification, Conversation, ConversationParticipant, DeviceToken, Message, MessageReadReceipt,
    NotificationPreference, ProviderAssignment, TypingIndicator,
)
from app.models.group_chat_model import (
    ClinicalRecordEntry, GroupAuditLog, GroupConsent, GroupDataRequest, GroupReport, MessageReaction, VerifiedProvider,
)
from app.models.notification_model import Notification, NotificationLog, UserNotification
from app.services import chat_user_names, group_chat_notify, group_chat_service

TENANT, OTHER_TENANT = uuid4(), uuid4()
PROGRAM_ID, EPISODE_ID = uuid4(), uuid4()

TABLES = [
    Conversation, ConversationParticipant, Message, MessageReadReceipt, ChatAttachment, ChatNotification,
    NotificationPreference, DeviceToken, TypingIndicator, GroupAuditLog, GroupConsent, GroupReport, MessageReaction,
    VerifiedProvider, ClinicalRecordEntry, GroupDataRequest, Notification, UserNotification, NotificationLog, ProviderAssignment,
]


def person(name, role, tenant=TENANT, **extra):
    """A logged-in identity. `role` is the platform role: admin (clinic admin), provider, customer (patient)."""
    return {"id": str(uuid4()), "role": role, "email": f"{name.lower().replace(' ', '.')}@example.test", "name": name,
            "tenant_id": str(tenant), **extra}


def uid(user) -> UUID:
    return UUID(user["id"])


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)  # ChatNotification.data
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[m.__table__ for m in TABLES])
    sessions = sessionmaker(bind=engine)
    db = sessions()

    events, notifications, pushes = [], [], []
    names: dict[str, str] = {}

    # --- everything that leaves the process is captured
    monkeypatch.setattr(group_chat_notify, "emit_group", lambda cid, event, payload: events.append(("group", str(cid), event, payload)))
    monkeypatch.setattr(group_chat_notify, "emit_user", lambda user, event, payload: events.append(("user", str(user), event, payload)))
    monkeypatch.setattr(group_chat_notify, "sync_rooms", lambda user, cid, *, join: events.append(("rooms", str(user), "join" if join else "leave", str(cid))))
    monkeypatch.setattr(
        "app.services.notification_service.create_automatic_notification",
        lambda _db, **kw: notifications.append(kw) or SimpleNamespace(id=uuid4()),
    )
    monkeypatch.setattr(
        "app.services.chat_notification_service.send_push_to_tokens",
        lambda tokens, **kw: pushes.append({"tokens": tokens, **kw}),
    )
    monkeypatch.setattr(
        "app.services.chat_notification_service._dispatch_email_notification",
        lambda *a, **k: pushes.append({"email": True}),
    )
    monkeypatch.setattr(
        "app.services.chat_notification_service._dispatch_sms_notification",
        lambda *a, **k: pushes.append({"sms": True}),
    )
    chat_user_names.clear_cache()
    # the people compliance reports are sent to (the identity service is not reachable from tests)
    recipients = SimpleNamespace(platform=[], clinic=[])
    monkeypatch.setattr("app.services.event_notification_service.resolve_platform_admin_user_ids", lambda token=None: list(recipients.platform))
    monkeypatch.setattr(
        "app.services.event_notification_service.resolve_enterprise_admin_user_ids",
        lambda _db, item, access_token=None: (list(recipients.clinic), item.tenant_id),
    )
    monkeypatch.setattr(
        chat_user_names, "resolve_display_names",
        lambda user_ids, **kw: {str(u): names.get(str(u)) for u in user_ids},
    )
    monkeypatch.setattr(group_chat_service, "_link_exists", lambda *a, **k: True)
    for key, value in (
        ("GROUP_MAX_MEMBERS_CARE", 15), ("GROUP_MAX_MEMBERS_PROGRAM", 50), ("GROUP_MAX_ADMINS", 3),
        ("GROUP_EDIT_WINDOW_MINUTES", 15), ("GROUP_REQUIRE_VERIFIED_PROVIDER", True), ("GROUP_PROGRAM_PATIENTS_CAN_POST", True),
        ("GROUP_NAME_BLOCKED_TERMS", "diabetes,cancer"), ("GROUP_EMERGENCY_NUMBER", "112"), ("GROUP_RETENTION_DAYS", 3650),
    ):
        monkeypatch.setattr(settings, key, value)

    # --- people. Names are what the identity service would say.
    admin = person("Clinic Admin", "admin")
    rao = person("Dr Rao", "provider")
    mehta = person("Dr Mehta", "provider")
    nair = person("Dr Nair", "provider")
    unverified = person("Dr Unverified", "provider")
    priya = person("Priya Sharma", "customer")
    ravi = person("Ravi Kumar", "customer")
    asha = person("Asha Iyer", "customer")
    stranger = person("Stranger Lee", "customer")
    outsider_admin = person("Other Clinic Admin", "admin", tenant=OTHER_TENANT)
    root = {"id": str(uuid4()), "role": "super_admin", "email": "root@example.test", "name": "Platform Root", "status": "active"}
    people = SimpleNamespace(admin=admin, rao=rao, mehta=mehta, nair=nair, unverified=unverified, priya=priya, ravi=ravi,
                             asha=asha, stranger=stranger, outsider_admin=outsider_admin, root=root)
    for p in vars(people).values():
        names[p["id"]] = p["name"]
    recipients.platform = [uid(root)]
    recipients.clinic = [uid(admin)]
    # the clinic's member list at the identity service: the owner, the clinicians, the learners (not the other clinic's admin)
    def member_record(user, role, status="active"):
        return {"userId": user["id"], "membershipId": str(uuid4()), "role": role, "membershipStatus": status, "name": user["name"]}

    clinic_members = [member_record(admin, "tenant_owner")]
    clinic_members += [member_record(u, "internal_user") for u in (rao, mehta, nair, unverified)]
    clinic_members += [member_record(u, "external_user") for u in (priya, ravi, asha)]
    monkeypatch.setattr(
        "app.services.event_notification_service.owning_tenant_users",
        lambda tenant, token: (list(clinic_members) if tenant == TENANT else [member_record(outsider_admin, "tenant_owner")], "stub"),
    )
    for p in (rao, mehta, nair):
        db.add(VerifiedProvider(user_id=uid(p), tenant_id=TENANT, status="verified", registration_number="REG-" + p["name"][-3:]))
    db.commit()

    app = FastAPI()
    app.include_router(group_chat.router, prefix="/api/v1/groups")
    app.include_router(conversation.router, prefix="/api/v1/conversations")
    app.include_router(conversation_messages.router, prefix="/api/v1/conversations")
    app.include_router(message.router, prefix="/api/v1/messages")
    app.include_router(typing_indicator.router, prefix="/api/v1/conversations")
    app.dependency_overrides[get_db] = lambda: db
    state = {"user": rao}
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    client = TestClient(app, raise_server_exceptions=False)

    def as_user(user):
        state["user"] = user
        return client

    w = SimpleNamespace(
        db=db, client=client, as_user=as_user, people=people, events=events, notifications=notifications, pushes=pushes,
        names=names, app=app, engine=engine, recipients=recipients, clinic_members=clinic_members, member_record=member_record,
    )
    yield w
    db.close()
    engine.dispose()


# ---------------------------------------------------------------- helpers the tests share

def make_group(w, owner=None, kind="program", invitees=(), name="Yoga batch Oct 2026", **extra):
    """Create a group through the real API as `owner` (default Dr Rao). Returns the response JSON."""
    owner = owner or w.people.rao
    body = {"name": name, "kind": kind, "context_type": "program" if kind == "program" else "care_episode",
            "context_id": str(PROGRAM_ID if kind == "program" else EPISODE_ID),
            "invitees": [u if "user_id" in u else {"user_id": u["id"]} for u in invitees], **extra}
    resp = w.as_user(owner).post("/api/v1/groups", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def accept(w, user, group_id, **body):
    resp = w.as_user(user).post(f"/api/v1/groups/{group_id}/accept", json={"consent": True, **body})
    assert resp.status_code == 200, resp.text
    return resp.json()


def joined_group(w, owner=None, kind="program", members=(), **extra):
    """A group whose invitees have all accepted."""
    group = make_group(w, owner, kind, invitees=list(members), **extra)
    for member in members:
        accept(w, member, group["id"])
    return group


def say(w, user, group_id, text="hello", **extra):
    resp = w.as_user(user).post(f"/api/v1/groups/{group_id}/messages", json={"content": text, **extra})
    return resp


def messages_of(w, user, group_id, **params):
    resp = w.as_user(user).get(f"/api/v1/groups/{group_id}/messages", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def texts(body):
    """The readable text of each message in a list response (system lines included)."""
    return [m["content"] if m["status"] == "visible" else m["placeholder"] for m in body["items"]]


def audit_actions(w, group_id, user=None):
    resp = w.as_user(user or w.people.rao).get(f"/api/v1/groups/{group_id}/audit", params={"page_size": 200})
    assert resp.status_code == 200, resp.text
    return [e["action"] for e in resp.json()["items"]]


def age_message(w, message_id, minutes):
    """Make a message `minutes` old (for the edit-window tests)."""
    row = w.db.get(Message, UUID(str(message_id)))
    row.created_at = datetime.utcnow() - timedelta(minutes=minutes)
    # the members joined before the message was sent, as they would have if that much time had really passed
    for member in w.db.query(ConversationParticipant).filter_by(conversation_id=row.conversation_id).all():
        if member.history_from is not None and member.history_from >= row.created_at:
            member.history_from = row.created_at - timedelta(seconds=1)
    w.db.commit()
