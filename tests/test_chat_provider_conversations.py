"""Production bug: GET /api/v1/conversations/provider returned empty items for
a provider with a conversation genuinely assigned to them
(assigned_provider_id == authenticated user id).

Root cause: chat_repo.get_provider_conversations() INNER JOINed Conversation
to ConversationParticipant before evaluating the
"assigned_provider_id == provider_id OR is-a-provider-participant" OR
condition. A conversation whose assigned_provider_id matches but that has no
ConversationParticipant row at all (or none for this specific provider) was
silently dropped by the join, regardless of the OR condition, because SQL
INNER JOIN requires a match to appear at all. Fixed by replacing the join
with an EXISTS subquery so the two OR-branches are evaluated independently,
as originally intended.

Also verifies the fix does not produce duplicate rows for a conversation with
multiple participants — a side effect of the same buggy INNER JOIN (every
joined participant row satisfied the join independently of the OR match).
"""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.token_auth import payload_to_user
from app.db.database import Base
from app.models.chat_model import Conversation, ConversationParticipant, Message
from app.repository import chat_repo


@pytest.fixture
def db():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[Conversation.__table__, ConversationParticipant.__table__, Message.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def _conversation(**overrides):
    defaults = dict(
        id=uuid4(), status="open", conversation_type="standard",
        created_by=uuid4(),
    )
    defaults.update(overrides)
    return Conversation(**defaults)


# --- 1 & 4: reproduces the exact reported bug — assigned but no participant row ---

def test_provider_sees_conversation_assigned_to_them_with_no_participant_row(db):
    provider_id = UUID('31a64f29-f2a9-42ee-814d-61af33b25e4b')
    conversation_id = UUID('5bbb7f78-7556-4997-b940-c6f0e2fe3884')
    conversation = _conversation(id=conversation_id, assigned_provider_id=provider_id)
    db.add(conversation)
    # Deliberately no ConversationParticipant row for anyone — matches the
    # exact reported shape (assigned_provider_id set, no participant record).
    db.commit()

    items, total = chat_repo.get_provider_conversations(db, provider_id)

    assert total == 1
    assert [item.id for item in items] == [conversation_id]


def test_provider_sees_conversation_assigned_to_them_when_only_customer_is_a_participant(db):
    """The more common real shape: a customer participant exists, but the
    assigned provider was never added as a ConversationParticipant row."""
    provider_id = uuid4()
    conversation = _conversation(assigned_provider_id=provider_id)
    db.add(conversation)
    db.add(ConversationParticipant(conversation_id=conversation.id, user_id=uuid4(), role="customer"))
    db.commit()

    items, total = chat_repo.get_provider_conversations(db, provider_id)

    assert total == 1
    assert items[0].id == conversation.id


def test_provider_sees_conversation_via_participant_role_without_assigned_provider_id(db):
    """The other OR-branch: no assigned_provider_id set, but this user is a
    provider-role participant — must remain unaffected by the fix."""
    provider_id = uuid4()
    conversation = _conversation(assigned_provider_id=None)
    db.add(conversation)
    db.add(ConversationParticipant(conversation_id=conversation.id, user_id=provider_id, role="provider"))
    db.commit()

    items, total = chat_repo.get_provider_conversations(db, provider_id)

    assert total == 1
    assert items[0].id == conversation.id


# --- 2 & 5: provider isolation — Provider A cannot see Provider B's conversations ---

def test_provider_a_cannot_see_provider_b_conversations(db):
    provider_a = uuid4()
    provider_b = uuid4()
    conv_a = _conversation(assigned_provider_id=provider_a)
    conv_b = _conversation(assigned_provider_id=provider_b)
    db.add_all([conv_a, conv_b])
    db.commit()

    items, total = chat_repo.get_provider_conversations(db, provider_a)

    assert total == 1
    assert items[0].id == conv_a.id

    items_b, total_b = chat_repo.get_provider_conversations(db, provider_b)
    assert total_b == 1
    assert items_b[0].id == conv_b.id


# --- 3: JWT identity resolves to the correct provider identity ---

def test_jwt_sub_claim_resolves_to_the_provider_identity_used_by_the_query():
    """`sub` (Keycloak user id) drives the provider identity used to query
    conversations — a legacy `id` claim (PostgreSQL/Invigorate application
    user id), even if present on the same token, must be ignored."""
    sub_id = "31a64f29-f2a9-42ee-814d-61af33b25e4b"
    legacy_id = "9a5b9b9b-9b9b-9b9b-9b9b-9b9b9b9b9b9b"
    user = payload_to_user({"id": legacy_id, "sub": sub_id, "role": "provider", "email": "provider@example.com"})
    assert user["id"] == sub_id
    assert chat_repo.get_provider_conversations.__module__  # sanity: repo importable
    resolved = UUID(str(user["id"]))
    assert resolved == UUID(sub_id)


def test_jwt_sub_claim_used_when_id_claim_absent():
    """Keycloak tokens carry `sub`, not `id` — payload_to_user must fall back to it."""
    user = payload_to_user({"sub": "31a64f29-f2a9-42ee-814d-61af33b25e4b", "role": "provider"})
    assert user["id"] == "31a64f29-f2a9-42ee-814d-61af33b25e4b"


# --- 6: pagination remains correct, including no duplicate rows for multi-participant conversations ---

def test_pagination_and_no_duplicate_rows_for_multi_participant_conversation(db):
    provider_id = uuid4()
    conversation = _conversation(assigned_provider_id=provider_id)
    db.add(conversation)
    # Three unrelated participants on the SAME conversation — the old INNER
    # JOIN would have produced 3 duplicate rows for this one conversation.
    db.add_all([
        ConversationParticipant(conversation_id=conversation.id, user_id=uuid4(), role="customer"),
        ConversationParticipant(conversation_id=conversation.id, user_id=uuid4(), role="observer"),
        ConversationParticipant(conversation_id=conversation.id, user_id=uuid4(), role="observer"),
    ])
    db.commit()

    items, total = chat_repo.get_provider_conversations(db, provider_id, page=1, page_size=20)
    assert total == 1
    assert len(items) == 1

    # Add enough additional assigned conversations to exercise real pagination.
    for _ in range(4):
        db.add(_conversation(assigned_provider_id=provider_id))
    db.commit()

    page1, total5 = chat_repo.get_provider_conversations(db, provider_id, page=1, page_size=2)
    page2, _ = chat_repo.get_provider_conversations(db, provider_id, page=2, page_size=2)
    page3, _ = chat_repo.get_provider_conversations(db, provider_id, page=3, page_size=2)
    assert total5 == 5
    assert len(page1) == 2 and len(page2) == 2 and len(page3) == 1
    all_ids = {c.id for c in page1} | {c.id for c in page2} | {c.id for c in page3}
    assert len(all_ids) == 5  # no duplicates across pages


# --- default status filter still excludes archived; explicit status filter still works ---

def test_default_query_excludes_archived_but_status_filter_returns_them(db):
    provider_id = uuid4()
    open_conv = _conversation(assigned_provider_id=provider_id, status="open")
    archived_conv = _conversation(assigned_provider_id=provider_id, status="archived")
    db.add_all([open_conv, archived_conv])
    db.commit()

    items, total = chat_repo.get_provider_conversations(db, provider_id)
    assert total == 1
    assert items[0].id == open_conv.id

    archived_items, archived_total = chat_repo.get_provider_conversations(db, provider_id, status="archived")
    assert archived_total == 1
    assert archived_items[0].id == archived_conv.id


def test_soft_deleted_conversation_excluded(db):
    provider_id = uuid4()
    db.add(_conversation(assigned_provider_id=provider_id, is_deleted=True))
    db.commit()

    items, total = chat_repo.get_provider_conversations(db, provider_id)
    assert total == 0
    assert items == []
