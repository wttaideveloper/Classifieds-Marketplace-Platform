"""DELETE /api/v1/conversations/{id}: delete a conversation for the person who asks.

The customer and the provider share one conversation, so the delete is per participant, like archiving: it leaves
the caller's inbox and clears their history, and the other participant, the messages and the admin view are
untouched. A new message brings the chat back (showing only what was said after the deletion).
"""
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import conversation as routes
from app.core.dependencies import get_current_user
from app.db.database import Base, get_db
from app.models.chat_model import Conversation, ConversationParticipant, Message, MessageReadReceipt
from app.schemas.chat_schema import MessageCreate
from app.services import chat_service, chat_user_names

CUSTOMER, PROVIDER, STRANGER = uuid4(), uuid4(), uuid4()
LONG_AGO = datetime(2026, 1, 1, 9, 0)


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(
        engine,
        tables=[Conversation.__table__, ConversationParticipant.__table__, Message.__table__, MessageReadReceipt.__table__],
    )
    session = sessionmaker(bind=engine)()
    chat_user_names.clear_cache()
    monkeypatch.setattr(chat_user_names.identity, "list_tenant_users", lambda *a, **k: [])
    monkeypatch.setattr(chat_user_names.identity, "fetch_internal_user_by_id", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "create_message_notifications", lambda *a, **k: None)
    yield session
    session.close()
    engine.dispose()


def user(user_id, role):
    return {"id": str(user_id), "role": role, "email": f"{role}@example.com"}


def conversation(session, *, provider_row=True, n=0, messages=("hello", "are you there?")):
    convo = Conversation(
        id=uuid4(), status="open", conversation_type="standard", created_by=CUSTOMER, assigned_provider_id=PROVIDER,
        subject="Yoga", updated_at=datetime(2026, 1, 1, 0, n),
    )
    session.add(convo)
    session.add(ConversationParticipant(conversation_id=convo.id, user_id=CUSTOMER, role="customer"))
    if provider_row:
        session.add(ConversationParticipant(conversation_id=convo.id, user_id=PROVIDER, role="provider"))
    for i, text in enumerate(messages):
        session.add(Message(
            id=uuid4(), conversation_id=convo.id, sender_id=CUSTOMER, content=text, created_at=LONG_AGO + timedelta(minutes=i),
        ))
    session.commit()
    return convo


def delete(session, who, role, convo):
    return chat_service.delete_conversation_service(session, user(who, role), convo.id)


def customer_list(session, status=None):
    return chat_service.list_conversations_service(session, user(CUSTOMER, "customer"), status_filter=status, page=1, page_size=50)


def provider_list(session, status=None):
    return chat_service.list_provider_conversations_service(session, user(PROVIDER, "provider"), status_filter=status, page=1, page_size=50)


def ids(page):
    return [item.id for item in page.items]


def messages_for(session, who, role, convo):
    page = chat_service.get_messages_service(session, user(who, role), convo.id, limit=50)
    return [m.content for m in page.items]


def send(session, who, role, convo, text):
    return chat_service.send_message_service(
        session, user(who, role), MessageCreate(conversation_id=convo.id, content=text, message_type="text")
    )


# --- the request ----------------------------------------------------------------------------------------------

def test_delete_returns_the_id_and_deleted_true(db):
    convo = conversation(db)

    result = delete(db, CUSTOMER, "customer", convo)

    assert result.model_dump() == {"id": convo.id, "deleted": True}


def test_customer_deletes_only_the_customer_loses_it(db):
    convo = conversation(db)

    delete(db, CUSTOMER, "customer", convo)

    assert ids(customer_list(db)) == [] and ids(customer_list(db, "archived")) == []
    assert ids(provider_list(db)) == [convo.id]
    assert messages_for(db, PROVIDER, "provider", convo) == ["hello", "are you there?"]


def test_provider_deletes_only_the_provider_loses_it(db):
    convo = conversation(db)

    delete(db, PROVIDER, "provider", convo)

    assert ids(provider_list(db)) == [] and ids(provider_list(db, "archived")) == []
    assert ids(customer_list(db)) == [convo.id]
    assert messages_for(db, CUSTOMER, "customer", convo) == ["hello", "are you there?"]


def test_the_assigned_provider_with_no_participant_row_can_delete(db):
    convo = conversation(db, provider_row=False)
    assert ids(provider_list(db)) == [convo.id]

    delete(db, PROVIDER, "provider", convo)

    assert ids(provider_list(db)) == [] and ids(customer_list(db)) == [convo.id]


def test_both_delete_and_the_conversation_itself_still_exists(db):
    """Nothing is destroyed: the record and the messages stay for admins."""
    convo = conversation(db)

    delete(db, CUSTOMER, "customer", convo)
    delete(db, PROVIDER, "provider", convo)

    assert ids(customer_list(db)) == [] and ids(provider_list(db)) == []
    db.expire_all()
    assert db.query(Conversation).filter_by(id=convo.id, is_deleted=False).count() == 1
    assert db.query(Message).filter_by(conversation_id=convo.id).count() == 2
    admin = chat_service.admin_list_conversations_service(db, page=1, page_size=20)
    assert ids(admin) == [convo.id]


def test_the_conversation_and_its_messages_are_gone_for_the_person_who_deleted_it(db):
    convo = conversation(db)
    delete(db, CUSTOMER, "customer", convo)

    with pytest.raises(HTTPException) as detail:
        chat_service.get_conversation_service(db, user(CUSTOMER, "customer"), convo.id)
    with pytest.raises(HTTPException) as listing:
        messages_for(db, CUSTOMER, "customer", convo)

    assert detail.value.status_code == 404 and listing.value.status_code == 404


def test_search_no_longer_finds_it(db):
    convo = conversation(db)
    delete(db, CUSTOMER, "customer", convo)

    found = chat_service.search_conversations_service(db, user(CUSTOMER, "customer"), search="Yoga", page=1, page_size=20)
    still = chat_service.search_conversations_service(db, user(PROVIDER, "provider"), search="Yoga", page=1, page_size=20)

    assert ids(found) == [] and ids(still) == [convo.id]


def test_a_status_filter_does_not_bring_it_back(db):
    convo = conversation(db)
    delete(db, CUSTOMER, "customer", convo)

    assert ids(customer_list(db, "open")) == []


# --- idempotent -----------------------------------------------------------------------------------------------

def test_deleting_twice_is_the_same_200_and_keeps_the_first_cut_off(db):
    convo = conversation(db)
    delete(db, CUSTOMER, "customer", convo)
    first = db.query(ConversationParticipant).filter_by(conversation_id=convo.id, user_id=CUSTOMER).one().deleted_at

    again = delete(db, CUSTOMER, "customer", convo)

    assert again.model_dump() == {"id": convo.id, "deleted": True}
    db.expire_all()
    assert db.query(ConversationParticipant).filter_by(conversation_id=convo.id, user_id=CUSTOMER).one().deleted_at == first


# --- who may delete -------------------------------------------------------------------------------------------

def test_a_stranger_cannot_delete_and_nothing_is_created(db):
    convo = conversation(db)

    with pytest.raises(HTTPException) as err:
        delete(db, STRANGER, "customer", convo)

    assert err.value.status_code == 403
    assert db.query(ConversationParticipant).filter_by(conversation_id=convo.id).count() == 2
    assert db.query(ConversationParticipant).filter_by(is_deleted=True).count() == 0


def test_unknown_conversation_is_404(db):
    with pytest.raises(HTTPException) as err:
        chat_service.delete_conversation_service(db, user(CUSTOMER, "customer"), uuid4())
    assert err.value.status_code == 404


# --- archive, unread ------------------------------------------------------------------------------------------

def test_deleting_an_archived_chat_clears_the_archive_flag(db):
    convo = conversation(db)
    chat_service.archive_conversation_service(db, user(CUSTOMER, "customer"), convo.id, archived=True)

    delete(db, CUSTOMER, "customer", convo)

    row = db.query(ConversationParticipant).filter_by(conversation_id=convo.id, user_id=CUSTOMER).one()
    assert row.is_deleted is True and row.is_archived is False and row.archived_at is None
    assert ids(customer_list(db, "archived")) == []


def test_deleting_clears_the_unread_count(db):
    convo = conversation(db)
    for i in range(3):
        db.add(Message(id=uuid4(), conversation_id=convo.id, sender_id=PROVIDER, content=f"p{i}", created_at=LONG_AGO + timedelta(hours=1, minutes=i)))
    db.commit()
    assert customer_list(db).items[0].unread_count == 3

    delete(db, CUSTOMER, "customer", convo)
    send(db, PROVIDER, "provider", convo, "after")  # brings it back

    assert customer_list(db).items[0].unread_count == 1  # only the new message, not the old unread ones


# --- it comes back with new messages only ----------------------------------------------------------------------

def test_a_new_message_from_the_other_person_brings_the_chat_back_with_only_the_new_message(db):
    convo = conversation(db)
    delete(db, CUSTOMER, "customer", convo)
    assert ids(customer_list(db)) == []

    send(db, PROVIDER, "provider", convo, "sorry for the wait")

    assert ids(customer_list(db)) == [convo.id]
    assert messages_for(db, CUSTOMER, "customer", convo) == ["sorry for the wait"]  # old history stays cleared
    assert messages_for(db, PROVIDER, "provider", convo) == ["hello", "are you there?", "sorry for the wait"]


def test_sending_after_deleting_reopens_the_chat_for_the_sender(db):
    convo = conversation(db)
    delete(db, CUSTOMER, "customer", convo)

    send(db, CUSTOMER, "customer", convo, "me again")

    assert ids(customer_list(db)) == [convo.id]
    assert messages_for(db, CUSTOMER, "customer", convo) == ["me again"]


def test_the_other_persons_chat_is_not_touched_by_a_new_message(db):
    convo = conversation(db)
    delete(db, CUSTOMER, "customer", convo)

    send(db, PROVIDER, "provider", convo, "hi")

    row = db.query(ConversationParticipant).filter_by(conversation_id=convo.id, user_id=PROVIDER).one()
    assert row.is_deleted is False and row.deleted_at is None


def test_message_search_ignores_what_was_said_before_the_deletion(db):
    convo = conversation(db, messages=("old booking question",))
    delete(db, CUSTOMER, "customer", convo)
    send(db, PROVIDER, "provider", convo, "new booking answer")

    page = chat_service.search_messages_service(db, user(CUSTOMER, "customer"), search="booking", page=1, page_size=20)

    assert [m.content for m in page.items] == ["new booking answer"]


def test_opening_the_chat_again_brings_it_back_empty(db):
    from app.schemas.chat_schema import ConversationCreate, ParticipantInput

    convo = conversation(db)
    delete(db, CUSTOMER, "customer", convo)

    created = chat_service.create_conversation_service(
        db, user(CUSTOMER, "customer"),
        ConversationCreate(participant_ids=[ParticipantInput(user_id=PROVIDER, role="provider")]),
    )

    assert created.id == convo.id and created.is_archived is False
    assert ids(customer_list(db)) == [convo.id]
    assert messages_for(db, CUSTOMER, "customer", convo) == []


# --- the real route -------------------------------------------------------------------------------------------

def test_the_http_route(db):
    convo = conversation(db)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1/conversations")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user(CUSTOMER, "customer")

    with TestClient(app) as client:
        first = client.delete(f"/api/v1/conversations/{convo.id}")
        again = client.delete(f"/api/v1/conversations/{convo.id}")
        missing = client.delete(f"/api/v1/conversations/{uuid4()}")
        detail = client.get(f"/api/v1/conversations/{convo.id}")

    assert first.status_code == 200 and first.json() == {"id": str(convo.id), "deleted": True}
    assert again.status_code == 200 and again.json() == first.json()
    assert missing.status_code == 404
    assert detail.status_code == 404
    assert ids(provider_list(db)) == [convo.id]
