"""customer_name on the conversation list and conversation detail responses.

Names are not stored in the chat tables; they come from the identity service. These tests stub that service
(no network) and check the response shape, the fallbacks and that the page still costs a constant number of
database queries.
"""

from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.models.chat_model import Conversation, ConversationParticipant, Message, MessageReadReceipt
from app.services import chat_service, chat_user_names

PROVIDER = uuid4()
TENANT = uuid4()


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(
        engine,
        tables=[Conversation.__table__, ConversationParticipant.__table__, Message.__table__, MessageReadReceipt.__table__],
    )
    session = sessionmaker(bind=engine)()
    yield session, engine
    session.close()
    engine.dispose()


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    """No network: tenant members and single-user lookups are served from these dicts."""
    chat_user_names.clear_cache()
    state = {"members": {}, "users": {}, "member_calls": 0, "user_calls": []}

    def list_tenant_users(tenant_id, access_token=None):
        state["member_calls"] += 1
        return state["members"].get(tenant_id, [])

    def fetch_internal_user_by_id(user_id):
        state["user_calls"].append(user_id)
        return state["users"].get(user_id)

    monkeypatch.setattr(chat_user_names.identity, "list_tenant_users", list_tenant_users)
    monkeypatch.setattr(chat_user_names.identity, "fetch_internal_user_by_id", fetch_internal_user_by_id)
    yield state
    chat_user_names.clear_cache()


def conversation(session, customer_id, *, tenant_id=None, with_customer_row=True, created_by=None, n=0):
    convo = Conversation(
        id=uuid4(), status="open", conversation_type="standard", tenant_id=tenant_id, subject="Yoga question",
        created_by=created_by or customer_id, assigned_provider_id=PROVIDER,
        updated_at=datetime(2026, 1, 1, 0, n % 59),
    )
    session.add(convo)
    if with_customer_row:
        session.add(ConversationParticipant(conversation_id=convo.id, user_id=customer_id, role="customer"))
    session.add(ConversationParticipant(conversation_id=convo.id, user_id=PROVIDER, role="provider"))
    session.add(Message(id=uuid4(), conversation_id=convo.id, sender_id=customer_id, content="hello"))
    session.commit()
    return convo


def provider_user():
    return {"id": str(PROVIDER), "role": "provider", "email": "p@example.com"}


def provider_page(session, user=None):
    return chat_service.list_provider_conversations_service(session, user or provider_user(), page=1, page_size=20)


# --- shape ---------------------------------------------------------------------------------------

def test_list_item_carries_customer_name(db, identity):
    session, _ = db
    customer = uuid4()
    convo = conversation(session, customer)
    identity["users"][str(customer)] = {"id": str(customer), "name": "Asha Rao"}

    [item] = provider_page(session).model_dump()["items"]

    assert item["id"] == convo.id
    assert item["customer_name"] == "Asha Rao"
    assert item["other_participant_user_id"] == customer  # the existing field is unchanged


def test_detail_carries_customer_name(db, identity):
    session, _ = db
    customer = uuid4()
    convo = conversation(session, customer)
    identity["users"][str(customer)] = {"data": {"firstName": "Asha", "lastName": "Rao"}}

    detail = chat_service.get_conversation_service(session, provider_user(), convo.id).model_dump()

    assert detail["customer_name"] == "Asha Rao"
    assert detail["created_by"] == customer


def test_the_user_list_search_and_archived_use_the_same_field(db, identity):
    session, _ = db
    customer = uuid4()
    conversation(session, customer)
    identity["users"][str(customer)] = {"name": "Asha Rao"}
    user = {"id": str(customer), "role": "customer", "name": "Asha Rao"}

    listed = chat_service.list_conversations_service(session, user, page=1, page_size=20).model_dump()["items"]
    found = chat_service.search_conversations_service(session, user, search="Yoga", page=1, page_size=20).model_dump()["items"]

    assert [i["customer_name"] for i in listed] == ["Asha Rao"]
    assert [i["customer_name"] for i in found] == ["Asha Rao"]


# --- where the name comes from -------------------------------------------------------------------

def test_the_callers_own_name_comes_from_their_token_with_no_lookup(db, identity):
    session, _ = db
    customer = uuid4()
    conversation(session, customer)
    user = {"id": str(customer), "role": "customer", "name": "Asha Rao"}

    [item] = chat_service.list_conversations_service(session, user, page=1, page_size=20).model_dump()["items"]

    assert item["customer_name"] == "Asha Rao"
    assert identity["user_calls"] == [] and identity["member_calls"] == 0


def test_tenant_members_are_read_once_for_the_whole_page(db, identity):
    session, _ = db
    customers = [uuid4() for _ in range(5)]
    for i, customer in enumerate(customers):
        conversation(session, customer, tenant_id=TENANT, n=i)
    identity["members"][TENANT] = [{"id": str(c), "name": f"Customer {i}"} for i, c in enumerate(customers)]

    names = {i["other_participant_user_id"]: i["customer_name"] for i in provider_page(session).model_dump()["items"]}

    assert names == {c: f"Customer {i}" for i, c in enumerate(customers)}
    assert identity["member_calls"] == 1 and identity["user_calls"] == []


def test_people_missing_from_the_member_list_are_looked_up_individually(db, identity):
    session, _ = db
    member, outsider = uuid4(), uuid4()
    conversation(session, member, tenant_id=TENANT, n=1)
    conversation(session, outsider, tenant_id=TENANT, n=2)
    identity["members"][TENANT] = [{"id": str(member), "name": "Member"}]
    identity["users"][str(outsider)] = {"name": "Outsider"}

    names = {i["other_participant_user_id"]: i["customer_name"] for i in provider_page(session).model_dump()["items"]}

    assert names == {member: "Member", outsider: "Outsider"}
    assert identity["user_calls"] == [str(outsider)]


def test_names_are_cached_between_requests(db, identity):
    session, _ = db
    customer = uuid4()
    conversation(session, customer)
    identity["users"][str(customer)] = {"name": "Asha Rao"}

    provider_page(session)
    provider_page(session)

    assert identity["user_calls"] == [str(customer)]


# --- when there is no name -----------------------------------------------------------------------

def test_unknown_user_gives_null_not_an_error(db, identity):
    session, _ = db
    conversation(session, uuid4())

    [item] = provider_page(session).model_dump()["items"]

    assert item["customer_name"] is None


def test_an_email_is_never_returned_as_a_name(db, identity):
    session, _ = db
    customer = uuid4()
    conversation(session, customer)
    identity["users"][str(customer)] = {"name": "asha@example.com", "email": "asha@example.com"}

    [item] = provider_page(session).model_dump()["items"]

    assert item["customer_name"] is None


def test_a_failing_identity_service_does_not_break_the_list(db, identity, monkeypatch):
    session, _ = db
    conversation(session, uuid4())

    def boom(user_id):
        raise RuntimeError("identity service down")

    monkeypatch.setattr(chat_user_names.identity, "fetch_internal_user_by_id", boom)

    [item] = provider_page(session).model_dump()["items"]

    assert item["customer_name"] is None


# --- which participant is the customer -----------------------------------------------------------

def test_the_customer_is_the_customer_role_participant_not_the_provider(db, identity):
    session, _ = db
    customer = uuid4()
    convo = conversation(session, customer)
    identity["users"][str(customer)] = {"name": "Asha Rao"}
    identity["users"][str(PROVIDER)] = {"name": "Dr Provider"}

    assert chat_service.get_conversation_service(session, provider_user(), convo.id).customer_name == "Asha Rao"


def test_with_no_participant_row_the_creator_is_the_customer(db, identity):
    session, _ = db
    customer = uuid4()
    conversation(session, customer, with_customer_row=False)
    identity["users"][str(customer)] = {"name": "Asha Rao"}

    [item] = provider_page(session).model_dump()["items"]

    assert item["customer_name"] == "Asha Rao"


def test_a_conversation_created_by_the_provider_with_no_customer_has_no_name(db, identity):
    session, _ = db
    conversation(session, uuid4(), with_customer_row=False, created_by=PROVIDER)

    [item] = provider_page(session).model_dump()["items"]

    assert item["customer_name"] is None


# --- cost ----------------------------------------------------------------------------------------

def test_query_count_does_not_grow_with_the_page(db, identity):
    session, engine = db
    executed = []

    def on_execute(*args, **kwargs):
        executed.append(1)

    def queries_for_one_page():
        executed.clear()
        event.listen(engine, "before_cursor_execute", on_execute)
        try:
            provider_page(session)
        finally:
            event.remove(engine, "before_cursor_execute", on_execute)
        return len(executed)

    for i in range(2):
        conversation(session, uuid4(), n=i)
    small = queries_for_one_page()
    for i in range(2, 12):
        conversation(session, uuid4(), n=i)
    large = queries_for_one_page()

    assert large == small
