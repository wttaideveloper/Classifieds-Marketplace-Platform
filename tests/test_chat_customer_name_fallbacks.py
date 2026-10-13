"""customer_name when the identity service cannot name the customer.

The identity service only lists people in the provider's tenant, so a customer from outside it came back with a null
name. The name now also comes from (1) what the person's own sign-in gave us when they used chat and (2) names they
typed into this system's own records, and it is looked up with the same id the list returns as other_participant_user_id.
"""

from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.models.address_model import UserAddress
from app.models.chat_model import (
    Conversation, ConversationParticipant, Message, MessageReadReceipt, UserDisplayName,
)
from app.services import chat_service, chat_user_names

PROVIDER = uuid4()


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(
        engine,
        tables=[
            Conversation.__table__, ConversationParticipant.__table__, Message.__table__, MessageReadReceipt.__table__,
            UserDisplayName.__table__, UserAddress.__table__,
        ],
    )
    session = sessionmaker(bind=engine)()
    yield session, engine
    session.close()
    engine.dispose()


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    """The identity service knows nobody unless a test says so."""
    chat_user_names.clear_cache()
    state = {"users": {}}

    monkeypatch.setattr(chat_user_names.identity, "list_tenant_users", lambda tenant_id, access_token=None: [])
    monkeypatch.setattr(chat_user_names.identity, "fetch_internal_user_by_id", lambda user_id: state["users"].get(user_id))
    yield state
    chat_user_names.clear_cache()


def conversation(session, customer_id, *, customer_row=True, n=0, kind="standard"):
    convo = Conversation(
        id=uuid4(), status="open", conversation_type=kind, subject="Yoga question",
        created_by=customer_id, assigned_provider_id=PROVIDER, updated_at=datetime(2026, 1, 1, 0, n % 59),
    )
    session.add(convo)
    if customer_row:
        session.add(ConversationParticipant(conversation_id=convo.id, user_id=customer_id, role="customer"))
    session.add(ConversationParticipant(conversation_id=convo.id, user_id=PROVIDER, role="provider"))
    session.add(Message(id=uuid4(), conversation_id=convo.id, sender_id=customer_id, content="hello"))
    session.commit()
    return convo


def customer_user(customer_id, name="Asha Rao"):
    return {"id": str(customer_id), "role": "customer", "name": name, "email": "asha@example.com"}


def provider_user():
    return {"id": str(PROVIDER), "role": "provider"}


def provider_items(session):
    return chat_service.list_provider_conversations_service(session, provider_user(), page=1, page_size=20).model_dump()["items"]


def address(session, user_id, full_name):
    session.add(UserAddress(
        user_id=user_id, full_name=full_name, phone="1", line1="1 Main St", city="Pune", state="MH", zip="411001",
        country="IN",
    ))
    session.commit()


# --- the name the customer's own sign-in gave us --------------------------------------------------

def test_a_customer_who_used_chat_is_named_for_the_provider(db):
    session, _ = db
    customer = uuid4()
    convo = conversation(session, customer)

    # the customer opens their inbox once; their token carries the name
    chat_service.list_conversations_service(session, customer_user(customer), page=1, page_size=20)
    chat_user_names.clear_cache()  # a different server process, a later day

    [item] = provider_items(session)
    detail = chat_service.get_conversation_service(session, provider_user(), convo.id)

    assert item["customer_name"] == "Asha Rao"
    assert item["other_participant_user_id"] == customer  # the id stays an id
    assert detail.customer_name == "Asha Rao"


def test_a_changed_name_is_updated(db):
    session, _ = db
    customer = uuid4()
    conversation(session, customer)

    chat_service.list_conversations_service(session, customer_user(customer, "Asha Rao"), page=1, page_size=20)
    chat_service.list_conversations_service(session, customer_user(customer, "Asha Menon"), page=1, page_size=20)
    chat_user_names.clear_cache()

    assert [i["customer_name"] for i in provider_items(session)] == ["Asha Menon"]
    assert session.query(UserDisplayName).count() == 1


def test_an_email_in_the_name_claim_is_never_saved(db):
    session, _ = db
    customer = uuid4()
    conversation(session, customer)

    chat_service.list_conversations_service(session, customer_user(customer, "asha@example.com"), page=1, page_size=20)
    chat_user_names.clear_cache()

    assert session.query(UserDisplayName).count() == 0
    assert [i["customer_name"] for i in provider_items(session)] == [None]


# --- names from this system's own records ---------------------------------------------------------

def test_a_name_the_customer_typed_into_an_address_is_used(db):
    session, _ = db
    customer = uuid4()
    conversation(session, customer)
    address(session, customer, "Asha Rao")

    assert [i["customer_name"] for i in provider_items(session)] == ["Asha Rao"]


def test_the_identity_service_wins_over_saved_and_record_names(db, identity, monkeypatch):
    session, _ = db
    customer = uuid4()
    conversation(session, customer)
    address(session, customer, "Old Address Name")
    session.add(UserDisplayName(user_id=customer, display_name="Old Saved Name", source="token"))
    session.commit()

    def members_of_tenant(tenant_id, access_token=None):
        return [{"id": str(customer), "name": "Asha Rao"}]

    monkeypatch.setattr(chat_user_names.identity, "list_tenant_users", members_of_tenant)
    session.query(Conversation).update({"tenant_id": uuid4()})
    session.commit()

    assert [i["customer_name"] for i in provider_items(session)] == ["Asha Rao"]


# --- the id the list returns is the id that gets named --------------------------------------------

def test_with_no_customer_row_the_other_participant_is_named(db, identity):
    session, _ = db
    other = uuid4()
    # created by the provider and no customer-role participant: the other participant is the only candidate
    convo = Conversation(
        id=uuid4(), status="open", conversation_type="standard", subject="Hello", created_by=PROVIDER,
        assigned_provider_id=PROVIDER, updated_at=datetime(2026, 1, 1),
    )
    session.add(convo)
    session.add(ConversationParticipant(conversation_id=convo.id, user_id=PROVIDER, role="provider"))
    session.add(ConversationParticipant(conversation_id=convo.id, user_id=other, role="admin"))
    session.commit()
    identity["users"][str(other)] = {"name": "Meera Iyer"}

    [item] = provider_items(session)
    detail = chat_service.get_conversation_service(session, provider_user(), convo.id)

    assert item["other_participant_user_id"] == other
    assert item["customer_name"] == "Meera Iyer"
    assert detail.customer_name == "Meera Iyer"


def test_a_group_conversation_has_no_customer_name(db, identity):
    session, _ = db
    member = uuid4()
    convo = conversation(session, member, customer_row=False, kind="group")
    identity["users"][str(member)] = {"name": "Group Member"}

    detail = chat_service.get_conversation_service(session, provider_user(), convo.id)

    assert detail.customer_name is None


# --- it must not break or slow chat ---------------------------------------------------------------

def test_a_missing_names_table_does_not_break_the_list(db):
    session, _ = db
    customer = uuid4()
    conversation(session, customer)
    UserDisplayName.__table__.drop(session.get_bind())

    assert [i["customer_name"] for i in provider_items(session)] == [None]
    chat_service.list_conversations_service(session, customer_user(customer), page=1, page_size=20)  # still answers


def test_query_count_does_not_grow_with_the_page(db):
    session, engine = db
    executed = []

    def on_execute(*args, **kwargs):
        executed.append(1)

    def queries_for_one_page():
        executed.clear()
        event.listen(engine, "before_cursor_execute", on_execute)
        try:
            provider_items(session)
        finally:
            event.remove(engine, "before_cursor_execute", on_execute)
        return len(executed)

    for i in range(2):
        conversation(session, uuid4(), n=i)
    small = queries_for_one_page()
    chat_user_names.clear_cache()
    for i in range(2, 12):
        conversation(session, uuid4(), n=i)
    large = queries_for_one_page()

    assert large == small
