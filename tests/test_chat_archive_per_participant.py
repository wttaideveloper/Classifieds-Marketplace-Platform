"""Archiving a conversation is per participant.

Web Provider and Mobile Customer share one conversation. Archiving it used to set status = 'archived' on the
conversation itself, so it vanished for the other person too. Now each participant has their own state
(conversation_participants.is_archived / archived_at).
"""
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.models.chat_model import Conversation, ConversationParticipant, Message, MessageReadReceipt
from app.services import chat_service, chat_user_names


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
    yield session
    session.close()
    engine.dispose()


CUSTOMER, PROVIDER, STRANGER = uuid4(), uuid4(), uuid4()


def user(user_id, role):
    return {"id": str(user_id), "role": role, "email": f"{role}@example.com"}


def conversation(session, *, provider_row=True, status="open", n=0):
    convo = Conversation(
        id=uuid4(), status=status, conversation_type="standard", created_by=CUSTOMER,
        assigned_provider_id=PROVIDER, subject="Yoga", updated_at=datetime(2026, 1, 1, 0, n),
    )
    session.add(convo)
    session.add(ConversationParticipant(conversation_id=convo.id, user_id=CUSTOMER, role="customer"))
    if provider_row:
        session.add(ConversationParticipant(conversation_id=convo.id, user_id=PROVIDER, role="provider"))
    session.add(Message(id=uuid4(), conversation_id=convo.id, sender_id=CUSTOMER, content="hi"))
    session.commit()
    return convo


def archive(session, who, role, convo, archived=True):
    return chat_service.archive_conversation_service(session, user(who, role), convo.id, archived=archived)


def customer_list(session, status=None):
    return chat_service.list_conversations_service(session, user(CUSTOMER, "customer"), status_filter=status, page=1, page_size=50)


def provider_list(session, status=None):
    return chat_service.list_provider_conversations_service(session, user(PROVIDER, "provider"), status_filter=status, page=1, page_size=50)


def ids(page):
    return [item.id for item in page.items]


# --- the behaviour that was asked for ---------------------------------------------------------------------------

def test_customer_archives_only_the_customer_sees_it_archived(db):
    convo = conversation(db)

    archive(db, CUSTOMER, "customer", convo)

    assert ids(customer_list(db)) == [] and ids(customer_list(db, "archived")) == [convo.id]
    assert ids(provider_list(db)) == [convo.id] and ids(provider_list(db, "archived")) == []


def test_provider_archives_only_the_provider_sees_it_archived(db):
    convo = conversation(db)

    archive(db, PROVIDER, "provider", convo)

    assert ids(provider_list(db)) == [] and ids(provider_list(db, "archived")) == [convo.id]
    assert ids(customer_list(db)) == [convo.id] and ids(customer_list(db, "archived")) == []


def test_both_archive_independently_and_each_has_it_in_their_own_archived_list(db):
    convo = conversation(db)

    archive(db, CUSTOMER, "customer", convo)
    archive(db, PROVIDER, "provider", convo)

    assert ids(customer_list(db, "archived")) == [convo.id] and ids(provider_list(db, "archived")) == [convo.id]
    assert ids(customer_list(db)) == [] and ids(provider_list(db)) == []


def test_unarchiving_restores_only_the_callers_own_state(db):
    convo = conversation(db)
    archive(db, CUSTOMER, "customer", convo)
    archive(db, PROVIDER, "provider", convo)

    archive(db, CUSTOMER, "customer", convo, archived=False)

    assert ids(customer_list(db)) == [convo.id] and ids(customer_list(db, "archived")) == []
    assert ids(provider_list(db, "archived")) == [convo.id]  # the provider's archive is untouched


# --- responses are relative to the caller -----------------------------------------------------------------------

def test_list_items_report_is_archived_for_the_caller(db):
    convo = conversation(db)
    archive(db, CUSTOMER, "customer", convo)

    [mine] = customer_list(db, "archived").items
    [theirs] = provider_list(db).items

    assert mine.is_archived is True and mine.archived_at is not None
    assert theirs.is_archived is False and theirs.archived_at is None


def test_detail_reports_is_archived_for_the_caller(db):
    convo = conversation(db)
    archive(db, PROVIDER, "provider", convo)

    as_provider = chat_service.get_conversation_service(db, user(PROVIDER, "provider"), convo.id)
    as_customer = chat_service.get_conversation_service(db, user(CUSTOMER, "customer"), convo.id)

    assert as_provider.is_archived is True and as_provider.archived_at is not None
    assert as_customer.is_archived is False and as_customer.archived_at is None


def test_archive_response_is_the_callers_state(db):
    convo = conversation(db)

    result = archive(db, CUSTOMER, "customer", convo)
    assert result.is_archived is True and result.archived_at is not None

    result = archive(db, CUSTOMER, "customer", convo, archived=False)
    assert result.is_archived is False and result.archived_at is None


def test_archiving_does_not_change_the_conversation_itself(db):
    convo = conversation(db)
    before = (convo.status, convo.updated_at, convo.archived_at)

    result = archive(db, CUSTOMER, "customer", convo)

    db.refresh(convo)
    assert (convo.status, convo.updated_at, convo.archived_at) == before
    assert result.status == "open" and result.updated_at == before[1]


def test_archiving_is_idempotent(db):
    convo = conversation(db)
    archive(db, CUSTOMER, "customer", convo)
    archive(db, CUSTOMER, "customer", convo)

    assert ids(customer_list(db, "archived")) == [convo.id]
    assert db.query(ConversationParticipant).filter_by(conversation_id=convo.id).count() == 2


def test_an_archived_chat_can_still_receive_and_send_messages(db):
    """Archived is a view setting, not a lock: the conversation stays open for the other person."""
    convo = conversation(db)
    archive(db, CUSTOMER, "customer", convo)

    db.refresh(convo)
    chat_service._validate_chat_rules(convo)  # raises when not open / expired / read-only


# --- who may archive --------------------------------------------------------------------------------------------

def test_a_stranger_cannot_archive(db):
    convo = conversation(db)

    with pytest.raises(HTTPException) as err:
        archive(db, STRANGER, "customer", convo)

    assert err.value.status_code == 403
    assert db.query(ConversationParticipant).filter_by(conversation_id=convo.id, is_archived=True).count() == 0
    assert db.query(ConversationParticipant).filter_by(conversation_id=convo.id).count() == 2  # no row was created


def test_a_stranger_cannot_unarchive(db):
    convo = conversation(db)
    archive(db, CUSTOMER, "customer", convo)

    with pytest.raises(HTTPException) as err:
        archive(db, STRANGER, "customer", convo, archived=False)

    assert err.value.status_code == 403
    assert ids(customer_list(db, "archived")) == [convo.id]


def test_unknown_conversation_is_404(db):
    with pytest.raises(HTTPException) as err:
        chat_service.archive_conversation_service(db, user(CUSTOMER, "customer"), uuid4(), archived=True)
    assert err.value.status_code == 404


def test_the_assigned_provider_without_a_participant_row_can_archive_for_themselves_only(db):
    convo = conversation(db, provider_row=False)
    assert ids(provider_list(db)) == [convo.id]  # listed because it is assigned to them

    archive(db, PROVIDER, "provider", convo)

    assert ids(provider_list(db)) == [] and ids(provider_list(db, "archived")) == [convo.id]
    assert ids(customer_list(db)) == [convo.id]


# --- archived chats can still be messaged -----------------------------------------------------------------------
# Production report: POST /socket-io/send-message answered 400 "Conversation is archived. Cannot send messages."

def send(session, who, role, convo, text="Hi"):
    from app.schemas.chat_schema import MessageCreate

    return chat_service.send_message_service(
        session, user(who, role), MessageCreate(conversation_id=convo.id, content=text, message_type="text")
    )


@pytest.fixture
def no_notifications(monkeypatch):
    monkeypatch.setattr(chat_service, "create_message_notifications", lambda *a, **k: None)


def test_the_person_who_archived_can_still_send(db, no_notifications):
    convo = conversation(db)
    archive(db, CUSTOMER, "customer", convo)

    sent = send(db, CUSTOMER, "customer", convo)

    assert sent["content"] == "Hi" and sent["sender_id"] == CUSTOMER


def test_the_other_person_can_send_into_a_chat_you_archived(db, no_notifications):
    convo = conversation(db)
    archive(db, PROVIDER, "provider", convo)

    sent = send(db, CUSTOMER, "customer", convo)

    assert sent["conversation_id"] == convo.id
    assert ids(provider_list(db, "archived")) == [convo.id]  # the provider's own archive is not undone


def test_a_chat_archived_the_old_way_can_still_be_messaged(db, no_notifications):
    """Rows whose status is still 'archived' because the archive migration has not run yet."""
    legacy = conversation(db, status="archived")

    assert send(db, CUSTOMER, "customer", legacy)["content"] == "Hi"
    assert send(db, PROVIDER, "provider", legacy, "Hello")["content"] == "Hello"


def test_a_closed_chat_still_refuses_messages(db, no_notifications):
    closed = conversation(db, status="closed")

    with pytest.raises(HTTPException) as err:
        send(db, CUSTOMER, "customer", closed)

    assert err.value.status_code == 400 and err.value.detail == "Conversation is closed. Cannot send messages."


def test_a_read_only_chat_still_refuses_messages(db, no_notifications):
    convo = conversation(db)
    convo.is_read_only = True
    db.commit()

    with pytest.raises(HTTPException) as err:
        send(db, CUSTOMER, "customer", convo)

    assert err.value.detail == "Conversation is read-only"


def test_a_stranger_still_cannot_send_into_an_archived_chat(db, no_notifications):
    convo = conversation(db)
    archive(db, CUSTOMER, "customer", convo)

    with pytest.raises(HTTPException) as err:
        send(db, STRANGER, "customer", convo)

    assert err.value.status_code == 403


# --- status filters and other lists still work ------------------------------------------------------------------

def test_status_filters_exclude_the_callers_archived_chats(db):
    open_convo = conversation(db, n=1)
    closed_convo = conversation(db, status="closed", n=2)
    archive(db, CUSTOMER, "customer", closed_convo)

    assert ids(customer_list(db, "open")) == [open_convo.id]
    assert ids(customer_list(db, "closed")) == []


def test_search_still_finds_an_archived_chat(db):
    convo = conversation(db)
    archive(db, CUSTOMER, "customer", convo)

    page = chat_service.search_conversations_service(db, user(CUSTOMER, "customer"), search="Yoga", page=1, page_size=20)

    assert ids(page) == [convo.id] and page.items[0].is_archived is True


def test_admin_list_marks_a_chat_archived_when_anyone_archived_it(db):
    archived = conversation(db, n=1)
    plain = conversation(db, n=2)
    archive(db, PROVIDER, "provider", archived)

    everything = chat_service.admin_list_conversations_service(db, page=1, page_size=20)
    only_archived = chat_service.admin_list_conversations_service(db, status_filter="archived", page=1, page_size=20)

    flags = {item.id: item.is_archived for item in everything.items}
    assert flags == {archived.id: True, plain.id: False}
    assert ids(only_archived) == [archived.id]


def test_dashboard_counts_chats_archived_by_anyone_once(db):
    convo = conversation(db)
    archive(db, CUSTOMER, "customer", convo)
    archive(db, PROVIDER, "provider", convo)
    conversation(db, n=2)

    stats = chat_service.get_admin_dashboard_service(db) if hasattr(chat_service, "get_admin_dashboard_service") else None
    if stats is None:
        from app.repository import chat_repo
        stats = chat_repo.get_admin_dashboard_stats(db)
    data = stats if isinstance(stats, dict) else stats.model_dump()

    assert data["archived_conversations"] == 1 and data["total_conversations"] == 2


# --- the migration's data step ----------------------------------------------------------------------------------

def test_migration_marks_every_participant_of_a_legacy_archived_chat_and_reopens_it(db):
    """Run the data step of the migration against a chat that was archived the old way."""
    import importlib.util
    import pathlib

    from sqlalchemy import text

    legacy = conversation(db, status="archived", provider_row=False)
    db.execute(text("UPDATE conversations SET archived_at = :t WHERE id = :id"), {"t": datetime(2026, 5, 1), "id": legacy.id.hex})
    db.commit()

    path = pathlib.Path(__file__).resolve().parent.parent / "alembic" / "versions" / "b6d1f3a8c2e4_conversation_participant_archive.py"
    spec = importlib.util.spec_from_file_location("archive_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # The columns already exist on the test tables, so only run the backfill half of upgrade().
    from unittest import mock
    with mock.patch.object(module.op, "add_column"), mock.patch.object(module.op, "create_index"), \
            mock.patch.object(module.op, "get_bind", return_value=db.connection()):
        module.upgrade()
    db.commit()
    db.expire_all()

    assert db.query(Conversation).filter_by(id=legacy.id).one().status == "open"
    rows = db.query(ConversationParticipant).filter_by(conversation_id=legacy.id).all()
    assert {r.user_id for r in rows} == {CUSTOMER, PROVIDER}  # the provider got a row so their state is kept
    assert all(r.is_archived and r.archived_at is not None for r in rows)
    assert ids(customer_list(db, "archived")) == [legacy.id] and ids(provider_list(db, "archived")) == [legacy.id]
