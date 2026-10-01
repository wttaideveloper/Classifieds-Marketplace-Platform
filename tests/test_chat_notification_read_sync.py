"""Chat read-state (PATCH /conversations/{id}/read, the mark_read socket
event, and its REST fallback POST /socket-io/mark-read — all of which funnel
through chat_service.mark_conversation_read_service / mark_message_read_service)
must also mark the matching chat_message rows in GET /users/me/notifications
read, so mobile doesn't have to fetch+filter+mark-read every notification
one by one after it already marked the conversation/messages read in chat."""
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.models.chat_model import ChatNotification, Conversation, ConversationParticipant, Message, MessageReadReceipt
from app.models.notification_model import Notification, UserNotification
from app.repository import notification_repo
from app.services.chat_service import mark_conversation_read_service, mark_message_read_service


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[
        Conversation.__table__, ConversationParticipant.__table__, Message.__table__,
        MessageReadReceipt.__table__, ChatNotification.__table__, Notification.__table__,
        UserNotification.__table__,
    ])
    sessions = sessionmaker(bind=engine)
    with sessions() as session:
        yield session
    engine.dispose()


def _make_conversation(db, user_id, other_id=None):
    conv = Conversation(id=uuid4(), status="open", conversation_type="standard", created_by=user_id)
    db.add(conv)
    db.add(ConversationParticipant(conversation_id=conv.id, user_id=user_id, role="customer"))
    if other_id:
        db.add(ConversationParticipant(conversation_id=conv.id, user_id=other_id, role="provider"))
    db.commit()
    return conv


def _make_message(db, conv_id, sender_id):
    msg = Message(id=uuid4(), conversation_id=conv_id, sender_id=sender_id, content="hi", message_type="text")
    db.add(msg)
    db.commit()
    return msg


def _make_chat_notification(db, user_id, conversation_id, message_id, *, category="chat_message", is_read=False):
    """Mirrors create_message_notifications' bridge call into
    create_automatic_notification — one Notification + its UserNotification row."""
    notif = Notification(
        id=uuid4(), title="New message", message="hi", notification_type="automatic",
        category=category, delivery_type="immediate", status="sent",
        metadata_json={"conversation_id": str(conversation_id), "message_id": str(message_id)},
    )
    db.add(notif)
    db.flush()
    user_notif = UserNotification(id=uuid4(), notification_id=notif.id, user_id=user_id, is_read=is_read)
    db.add(user_notif)
    db.commit()
    return notif, user_notif


def _current_user(user_id):
    return {"id": str(user_id), "role": "customer", "email": "learner@example.com"}


# --- notification_repo.mark_user_notifications_read_by_conversation (unit) ---

def test_repo_marks_matching_unread_notifications_read(db):
    user_id = uuid4()
    conv = _make_conversation(db, user_id)
    msg = _make_message(db, conv.id, user_id)
    _, un = _make_chat_notification(db, user_id, conv.id, msg.id)

    count = notification_repo.mark_user_notifications_read_by_conversation(db, user_id, conv.id)
    assert count == 1
    db.refresh(un)
    assert un.is_read is True
    assert un.read_at is not None


def test_repo_only_marks_matching_conversation(db):
    user_id = uuid4()
    conv_a = _make_conversation(db, user_id)
    conv_b = _make_conversation(db, user_id)
    msg_a = _make_message(db, conv_a.id, user_id)
    msg_b = _make_message(db, conv_b.id, user_id)
    _, un_a = _make_chat_notification(db, user_id, conv_a.id, msg_a.id)
    _, un_b = _make_chat_notification(db, user_id, conv_b.id, msg_b.id)

    notification_repo.mark_user_notifications_read_by_conversation(db, user_id, conv_a.id)
    db.refresh(un_a)
    db.refresh(un_b)
    assert un_a.is_read is True
    assert un_b.is_read is False  # a different conversation — untouched


def test_repo_only_marks_matching_user(db):
    user_a, user_b = uuid4(), uuid4()
    conv = _make_conversation(db, user_a, user_b)
    msg = _make_message(db, conv.id, user_a)
    _, un_a = _make_chat_notification(db, user_a, conv.id, msg.id)
    _, un_b = _make_chat_notification(db, user_b, conv.id, msg.id)

    notification_repo.mark_user_notifications_read_by_conversation(db, user_a, conv.id)
    db.refresh(un_a)
    db.refresh(un_b)
    assert un_a.is_read is True
    assert un_b.is_read is False  # different user's copy of the same conversation's notification


def test_repo_does_not_touch_other_categories(db):
    """Other notification categories (bookings, courses, etc.) must not be affected."""
    user_id = uuid4()
    conv = _make_conversation(db, user_id)
    msg = _make_message(db, conv.id, user_id)
    _make_chat_notification(db, user_id, conv.id, msg.id)
    _, booking_un = _make_chat_notification(db, user_id, conv.id, msg.id, category="enrolment_approved")

    notification_repo.mark_user_notifications_read_by_conversation(db, user_id, conv.id)
    db.refresh(booking_un)
    assert booking_un.is_read is False


def test_repo_leaves_already_read_notifications_alone(db):
    user_id = uuid4()
    conv = _make_conversation(db, user_id)
    msg = _make_message(db, conv.id, user_id)
    notif, un = _make_chat_notification(db, user_id, conv.id, msg.id, is_read=True)
    original_read_at = un.read_at  # None, since the fixture doesn't set it even when is_read=True

    count = notification_repo.mark_user_notifications_read_by_conversation(db, user_id, conv.id)
    assert count == 0  # nothing to update — already read


def test_repo_returns_zero_for_conversation_with_no_notifications(db):
    user_id = uuid4()
    conv = _make_conversation(db, user_id)
    assert notification_repo.mark_user_notifications_read_by_conversation(db, user_id, conv.id) == 0


# --- end-to-end: the actual chat read-marking service functions trigger the sync ---

def test_conversation_read_endpoint_marks_notifications_read(db):
    user_id = uuid4()
    conv = _make_conversation(db, user_id)
    msg = _make_message(db, conv.id, user_id)
    _, un = _make_chat_notification(db, user_id, conv.id, msg.id)

    mark_conversation_read_service(db, _current_user(user_id), conv.id)

    db.refresh(un)
    assert un.is_read is True


def test_message_read_marks_notifications_read_for_its_conversation(db):
    """Mobile's actual REST fallback payload is {message_id, conversation_id}
    together — process_mark_read takes the message_id branch in that case, so
    this is the path real traffic hits, not just the conversation-only one."""
    user_id = uuid4()
    conv = _make_conversation(db, user_id)
    msg = _make_message(db, conv.id, user_id)
    _, un = _make_chat_notification(db, user_id, conv.id, msg.id)

    mark_message_read_service(db, _current_user(user_id), msg.id)

    db.refresh(un)
    assert un.is_read is True


def test_message_read_marks_other_unread_notifications_in_same_conversation(db):
    """A notification tied to a different message in the SAME conversation
    must also clear — conversation-level sync, not strict message-id matching."""
    user_id = uuid4()
    conv = _make_conversation(db, user_id)
    msg1 = _make_message(db, conv.id, user_id)
    msg2 = _make_message(db, conv.id, user_id)
    _, un1 = _make_chat_notification(db, user_id, conv.id, msg1.id)
    _, un2 = _make_chat_notification(db, user_id, conv.id, msg2.id)

    mark_message_read_service(db, _current_user(user_id), msg1.id)

    db.refresh(un1)
    db.refresh(un2)
    assert un1.is_read is True
    assert un2.is_read is True


def test_unread_count_drops_after_conversation_marked_read(db):
    user_id = uuid4()
    conv = _make_conversation(db, user_id)
    msg = _make_message(db, conv.id, user_id)
    _make_chat_notification(db, user_id, conv.id, msg.id)

    assert notification_repo.count_unread_user_notifications(db, user_id) == 1
    mark_conversation_read_service(db, _current_user(user_id), conv.id)
    assert notification_repo.count_unread_user_notifications(db, user_id) == 0
