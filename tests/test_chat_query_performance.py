"""Production issue: GET /conversations/provider, /conversations/provider/archived,
and /presence/online took 2-30+ seconds under load. Root cause (this repo):
list_provider_conversations_service (and list_conversations_service /
search_conversations_service, which share the same helper) issued one query per
conversation for the latest message, one per conversation for the caller's own
participant row, and one COUNT(*) per conversation for unread messages — an N+1
pattern that multiplied a single page-of-20 request into 60+ sequential DB
round trips, holding a pooled connection the whole time. Fixed by batching all
three into a handful of queries regardless of page size.

These tests assert the query count stays low and CONSTANT as the number of
conversations on a page grows, and that the response shape is unchanged.
"""

from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.models.chat_model import Conversation, ConversationParticipant, Message, MessageReadReceipt
from app.services import chat_service


@pytest.fixture
def db():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[Conversation.__table__, ConversationParticipant.__table__, Message.__table__, MessageReadReceipt.__table__])
    session = sessionmaker(bind=engine)()
    yield session, engine
    session.close()
    engine.dispose()


class _QueryCounter:
    def __init__(self, engine):
        self.count = 0
        self.engine = engine

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._on_execute)

    def _on_execute(self, *args, **kwargs):
        self.count += 1


def _seed_provider_conversations(db, provider_id, user_id, n):
    for i in range(n):
        conversation = Conversation(
            id=uuid4(), status="open", conversation_type="standard",
            created_by=user_id, assigned_provider_id=provider_id,
            updated_at=datetime(2026, 1, 1, 0, i % 59),
        )
        db.add(conversation)
        db.add(ConversationParticipant(conversation_id=conversation.id, user_id=user_id, role="customer"))
        db.add(ConversationParticipant(conversation_id=conversation.id, user_id=provider_id, role="provider"))
        db.add(Message(id=uuid4(), conversation_id=conversation.id, sender_id=user_id, content=f"hello {i}"))
    db.commit()


def test_provider_conversations_query_count_does_not_scale_with_page_size(db):
    session, engine = db
    provider_id = uuid4()
    user = {"id": str(uuid4()), "role": "provider", "email": "p@example.com"}

    _seed_provider_conversations(session, provider_id, uuid4(), 2)
    with _QueryCounter(engine) as counter:
        chat_service.list_provider_conversations_service(session, {**user, "id": str(provider_id)}, page=1, page_size=20)
    small_count = counter.count

    _seed_provider_conversations(session, provider_id, uuid4(), 10)  # 2 + 10 = 12 conversations now
    with _QueryCounter(engine) as counter2:
        chat_service.list_provider_conversations_service(session, {**user, "id": str(provider_id)}, page=1, page_size=20)
    large_count = counter2.count

    # Before the fix this scaled linearly with conversation count (N+1 for latest
    # message, participant, and unread count each). After the fix it must stay
    # bounded — a handful of batched queries regardless of how many conversations
    # are on the page, not "small_count + 10 more conversations worth of queries".
    assert large_count <= small_count + 3, (
        f"query count grew from {small_count} (2 conversations) to {large_count} "
        f"(12 conversations) — looks like an N+1 regression"
    )


def test_provider_conversations_response_shape_unchanged(db):
    session, engine = db
    provider_id = uuid4()
    user_id = uuid4()
    _seed_provider_conversations(session, provider_id, user_id, 3)

    result = chat_service.list_provider_conversations_service(
        session, {"id": str(provider_id), "role": "provider", "email": "p@example.com"}, page=1, page_size=20
    )
    payload = result.model_dump(mode="json")
    assert set(payload.keys()) == {"items", "pagination"}
    assert payload["pagination"]["total"] == 3
    item = payload["items"][0]
    expected_keys = {
        "id", "status", "conversation_type", "subject", "last_message_at",
        "last_message_preview", "last_message", "unread_count",
        "assigned_provider_id", "other_participant_user_id",
        "is_archived", "archived_at", "updated_at",
    }
    assert expected_keys.issubset(item.keys())
    assert item["unread_count"] == 1  # one message from the customer, unread by the provider


def test_provider_archived_conversations_response_shape_unchanged(db):
    session, engine = db
    provider_id = uuid4()
    conversation = Conversation(
        id=uuid4(), status="archived", conversation_type="standard",
        created_by=uuid4(), assigned_provider_id=provider_id,
    )
    session.add(conversation)
    session.commit()

    result = chat_service.list_provider_conversations_service(
        session, {"id": str(provider_id), "role": "provider", "email": "p@example.com"},
        status_filter="archived", page=1, page_size=20,
    )
    payload = result.model_dump(mode="json")
    assert payload["pagination"]["total"] == 1
    assert payload["items"][0]["status"] == "archived"
    assert payload["items"][0]["is_archived"] is True


def test_unread_count_matches_previous_per_conversation_semantics(db):
    """The batched unread-count query must match the old per-conversation
    count_unread_messages() behaviour exactly: messages not sent by the
    caller, not deleted, newer than their last_read_at (or all of them if
    they have never read the conversation)."""
    session, engine = db
    provider_id = uuid4()
    customer_id = uuid4()
    conversation = Conversation(id=uuid4(), status="open", conversation_type="standard", created_by=customer_id, assigned_provider_id=provider_id)
    session.add(conversation)
    session.add(ConversationParticipant(conversation_id=conversation.id, user_id=provider_id, role="provider", last_read_at=datetime(2026, 1, 1, 12, 0)))
    session.add(Message(id=uuid4(), conversation_id=conversation.id, sender_id=customer_id, content="before read", created_at=datetime(2026, 1, 1, 11, 0)))
    session.add(Message(id=uuid4(), conversation_id=conversation.id, sender_id=customer_id, content="after read", created_at=datetime(2026, 1, 1, 13, 0)))
    session.add(Message(id=uuid4(), conversation_id=conversation.id, sender_id=provider_id, content="my own message", created_at=datetime(2026, 1, 1, 14, 0)))
    session.commit()

    result = chat_service.list_provider_conversations_service(
        session, {"id": str(provider_id), "role": "provider", "email": "p@example.com"}, page=1, page_size=20
    )
    assert result.items[0].unread_count == 1  # only the post-last_read_at customer message
