from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, joinedload

from app.models.chat_model import (
    ChatAttachment,
    ChatNotification,
    ChatSubscription,
    Conversation,
    ConversationParticipant,
    DeviceToken,
    Message,
    MessageReadReceipt,
    NotificationPreference,
    ProviderAssignment,
    TypingIndicator,
    UserPresence,
)
from app.repository.query_utils import (
    apply_ilike_search,
    apply_pagination,
    apply_soft_delete_filter,
    build_pagination_meta,
    paginate_query,
)

DELETED_MESSAGE_PREVIEW = "deleted this message"


def _uuid(value) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def message_list_preview(message: Message | None) -> str | None:
    if not message:
        return None
    if message.is_deleted:
        return DELETED_MESSAGE_PREVIEW
    return (message.content or "")[:500]


def get_latest_conversation_message(
    db: Session,
    conversation_id: UUID,
) -> Message | None:
    return (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .first()
    )


def resolve_conversation_preview(
    db: Session,
    conversation: Conversation,
) -> tuple[str | None, datetime | None]:
    """Derive preview from the latest message (handles deleted/stale stored previews)."""
    latest = get_latest_conversation_message(db, conversation.id)
    if latest:
        return message_list_preview(latest), latest.created_at
    return conversation.last_message_preview, conversation.last_message_at


def get_latest_messages_for_conversations(
    db: Session,
    conversation_ids: list[UUID],
) -> dict[UUID, Message]:
    if not conversation_ids:
        return {}

    latest_by_conversation: dict[UUID, Message] = {}
    for conversation_id in conversation_ids:
        message = get_latest_conversation_message(db, conversation_id)
        if message:
            latest_by_conversation[conversation_id] = message
    return latest_by_conversation


def sync_conversation_last_message(db: Session, conversation_id: UUID) -> Conversation | None:
    conversation = get_conversation_by_id(db, conversation_id)
    if not conversation:
        return None

    latest = get_latest_conversation_message(db, conversation_id)
    if latest:
        conversation.last_message_preview = message_list_preview(latest)
        conversation.last_message_at = latest.created_at
    else:
        conversation.last_message_preview = None
        conversation.last_message_at = None

    return save_conversation(db, conversation)


def get_conversation_by_id(
    db: Session,
    conversation_id: UUID,
    *,
    with_participants: bool = False,
) -> Conversation | None:
    query = db.query(Conversation).filter(Conversation.id == conversation_id)
    query = apply_soft_delete_filter(query, Conversation, False)
    if with_participants:
        query = query.options(joinedload(Conversation.participants))
    return query.first()


def get_user_conversations(
    db: Session,
    user_id: UUID,
    *,
    status: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
):
    query = (
        db.query(Conversation)
        .join(ConversationParticipant)
        .filter(ConversationParticipant.user_id == user_id)
    )
    query = apply_soft_delete_filter(query, Conversation, False)

    if status:
        query = query.filter(Conversation.status == status)
    else:
        query = query.filter(Conversation.status != "archived")
    if search:
        query = apply_ilike_search(
            query,
            [Conversation.subject, Conversation.last_message_preview],
            search,
        )

    query = query.order_by(Conversation.updated_at.desc())
    return paginate_query(query, page, page_size)


def get_provider_conversations(
    db: Session,
    provider_id: UUID,
    *,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
):
    query = (
        db.query(Conversation)
        .join(ConversationParticipant)
        .filter(
            or_(
                Conversation.assigned_provider_id == provider_id,
                and_(
                    ConversationParticipant.user_id == provider_id,
                    ConversationParticipant.role == "provider",
                ),
            )
        )
    )
    query = apply_soft_delete_filter(query, Conversation, False)

    if status:
        query = query.filter(Conversation.status == status)
    else:
        query = query.filter(Conversation.status != "archived")

    query = query.order_by(Conversation.updated_at.desc())
    return paginate_query(query, page, page_size)


def find_existing_conversation(
    db: Session,
    user_id: UUID,
    participant_ids: list[UUID],
    context_type: str | None = None,
    context_id: UUID | None = None,
) -> Conversation | None:
    all_user_ids = {user_id, *participant_ids}
    query = (
        db.query(Conversation)
        .join(ConversationParticipant)
        .filter(ConversationParticipant.user_id.in_(all_user_ids))
    )
    query = apply_soft_delete_filter(query, Conversation, False)

    if context_type and context_id:
        query = query.filter(
            Conversation.context_type == context_type,
            Conversation.context_id == context_id,
        )

    candidates = query.options(joinedload(Conversation.participants)).all()
    for conv in candidates:
        conv_user_ids = {p.user_id for p in conv.participants}
        if conv_user_ids == all_user_ids and conv.status == "open":
            return conv
    return None


def create_conversation(db: Session, conversation: Conversation) -> Conversation:
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def save_conversation(db: Session, conversation: Conversation) -> Conversation:
    conversation.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(conversation)
    return conversation


def is_participant(db: Session, conversation_id: UUID, user_id: UUID) -> bool:
    return (
        db.query(ConversationParticipant)
        .filter(
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.user_id == user_id,
        )
        .first()
        is not None
    )


def get_participant(
    db: Session,
    conversation_id: UUID,
    user_id: UUID,
) -> ConversationParticipant | None:
    return (
        db.query(ConversationParticipant)
        .filter(
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.user_id == user_id,
        )
        .first()
    )


def count_unread_messages(
    db: Session,
    conversation_id: UUID,
    user_id: UUID,
    last_read_at: datetime | None,
) -> int:
    query = db.query(Message).filter(
        Message.conversation_id == conversation_id,
        Message.sender_id != user_id,
        Message.is_deleted.is_(False),
    )
    if last_read_at:
        query = query.filter(Message.created_at > last_read_at)
    return query.count()


def create_message(db: Session, message: Message) -> Message:
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def get_message_by_id(db: Session, message_id: UUID) -> Message | None:
    return db.query(Message).filter(Message.id == message_id).first()


def get_conversation_messages(
    db: Session,
    conversation_id: UUID,
    *,
    cursor: str | None = None,
    limit: int = 50,
):
    query = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
    )

    if cursor:
        cursor_dt, cursor_id = _parse_cursor(cursor)
        query = query.filter(
            or_(
                Message.created_at < cursor_dt,
                and_(Message.created_at == cursor_dt, Message.id < cursor_id),
            )
        )

    items = query.limit(limit + 1).all()
    has_more = len(items) > limit
    if has_more:
        items = items[:limit]

    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = _build_cursor(last.created_at, last.id)

    return list(reversed(items)), has_more, next_cursor


def _build_cursor(created_at: datetime, message_id: UUID) -> str:
    return f"{created_at.isoformat()}|{message_id}"


def _parse_cursor(cursor: str) -> tuple[datetime, UUID]:
    parts = cursor.split("|", 1)
    return datetime.fromisoformat(parts[0]), UUID(parts[1])


def search_messages(
    db: Session,
    user_id: UUID,
    *,
    search: str,
    conversation_id: UUID | None = None,
    provider_id: UUID | None = None,
    page: int = 1,
    page_size: int = 20,
):
    query = (
        db.query(Message)
        .join(Conversation)
        .join(ConversationParticipant)
        .filter(
            ConversationParticipant.user_id == user_id,
            Message.is_deleted.is_(False),
            Message.content.ilike(f"%{search.strip()}%"),
        )
    )
    query = apply_soft_delete_filter(query, Conversation, False)

    if conversation_id:
        query = query.filter(Message.conversation_id == conversation_id)
    if provider_id:
        query = query.filter(Conversation.assigned_provider_id == provider_id)

    query = query.order_by(Message.created_at.desc())
    return paginate_query(query, page, page_size)


def search_conversations(
    db: Session,
    user_id: UUID,
    *,
    search: str,
    provider_id: UUID | None = None,
    page: int = 1,
    page_size: int = 20,
):
    query = (
        db.query(Conversation)
        .join(ConversationParticipant)
        .filter(ConversationParticipant.user_id == user_id)
    )
    query = apply_soft_delete_filter(query, Conversation, False)
    query = apply_ilike_search(
        query,
        [Conversation.subject, Conversation.last_message_preview],
        search,
    )

    if provider_id:
        query = query.filter(Conversation.assigned_provider_id == provider_id)

    query = query.order_by(Conversation.updated_at.desc())
    return paginate_query(query, page, page_size)


def mark_message_read(
    db: Session,
    message_id: UUID,
    user_id: UUID,
) -> MessageReadReceipt:
    existing = (
        db.query(MessageReadReceipt)
        .filter(
            MessageReadReceipt.message_id == message_id,
            MessageReadReceipt.user_id == user_id,
        )
        .first()
    )
    if existing:
        return existing

    receipt = MessageReadReceipt(message_id=message_id, user_id=user_id)
    db.add(receipt)
    db.commit()
    db.refresh(receipt)
    return receipt


def mark_conversation_read(
    db: Session,
    conversation_id: UUID,
    user_id: UUID,
) -> ConversationParticipant:
    participant = get_participant(db, conversation_id, user_id)
    if participant:
        participant.last_read_at = datetime.utcnow()
        db.commit()
        db.refresh(participant)
    return participant


def get_message_read_receipts(
    db: Session,
    message_id: UUID,
) -> list[MessageReadReceipt]:
    return (
        db.query(MessageReadReceipt)
        .filter(MessageReadReceipt.message_id == message_id)
        .all()
    )


def soft_delete_message(db: Session, message: Message) -> Message:
    message.is_deleted = True
    message.deleted_at = datetime.utcnow()
    db.commit()
    db.refresh(message)
    return message


def update_message(db: Session, message: Message, *, content: str) -> Message:
    message.content = content
    message.is_edited = True
    message.edited_at = datetime.utcnow()
    message.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(message)
    return message


def create_attachment(db: Session, attachment: ChatAttachment) -> ChatAttachment:
    db.add(attachment)
    db.commit()
    db.refresh(attachment)
    return attachment


def get_attachment_by_id(db: Session, attachment_id: UUID) -> ChatAttachment | None:
    return (
        db.query(ChatAttachment)
        .filter(
            ChatAttachment.id == attachment_id,
            ChatAttachment.is_deleted.is_(False),
        )
        .first()
    )


def soft_delete_attachment(db: Session, attachment: ChatAttachment) -> ChatAttachment:
    attachment.is_deleted = True
    db.commit()
    db.refresh(attachment)
    return attachment


def save_attachment_transcript(
    db: Session,
    attachment: ChatAttachment,
    transcript: str,
) -> ChatAttachment:
    attachment.transcript = transcript.strip()
    attachment.transcribed_at = datetime.utcnow()
    db.commit()
    db.refresh(attachment)
    return attachment


def assign_provider(
    db: Session,
    conversation_id: UUID,
    provider_id: UUID,
    assigned_by: UUID,
) -> ProviderAssignment:
    db.query(ProviderAssignment).filter(
        ProviderAssignment.conversation_id == conversation_id,
        ProviderAssignment.status == "active",
    ).update({"status": "reassigned", "reassigned_at": datetime.utcnow()})

    assignment = ProviderAssignment(
        conversation_id=conversation_id,
        provider_id=provider_id,
        assigned_by=assigned_by,
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    return assignment


def get_active_provider_assignment(
    db: Session,
    conversation_id: UUID,
) -> ProviderAssignment | None:
    return (
        db.query(ProviderAssignment)
        .filter(
            ProviderAssignment.conversation_id == conversation_id,
            ProviderAssignment.status == "active",
        )
        .order_by(ProviderAssignment.assigned_at.desc())
        .first()
    )


def register_device_token(
    db: Session,
    user_id: UUID,
    token: str,
    platform: str,
) -> DeviceToken:
    existing = db.query(DeviceToken).filter(DeviceToken.token == token).first()
    if existing:
        existing.user_id = user_id
        existing.platform = platform
        existing.is_active = True
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing

    device = DeviceToken(user_id=user_id, token=token, platform=platform)
    db.add(device)
    db.commit()
    db.refresh(device)
    return device


def deactivate_device_token(db: Session, user_id: UUID, token: str) -> bool:
    device = (
        db.query(DeviceToken)
        .filter(DeviceToken.user_id == user_id, DeviceToken.token == token)
        .first()
    )
    if not device:
        return False
    device.is_active = False
    device.updated_at = datetime.utcnow()
    db.commit()
    return True


def get_notification_preferences(
    db: Session,
    user_id: UUID,
) -> NotificationPreference:
    prefs = (
        db.query(NotificationPreference)
        .filter(NotificationPreference.user_id == user_id)
        .first()
    )
    if not prefs:
        prefs = NotificationPreference(user_id=user_id)
        db.add(prefs)
        db.commit()
        db.refresh(prefs)
    return prefs


def update_notification_preferences(
    db: Session,
    user_id: UUID,
    updates: dict,
) -> NotificationPreference:
    prefs = get_notification_preferences(db, user_id)
    for key, value in updates.items():
        if value is not None and hasattr(prefs, key):
            setattr(prefs, key, value)
    prefs.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(prefs)
    return prefs


def count_unread_notifications(db: Session, user_id: UUID) -> int:
    return (
        db.query(ChatNotification)
        .filter(
            ChatNotification.user_id == user_id,
            ChatNotification.is_read.is_(False),
        )
        .count()
    )


def count_total_unread_messages(db: Session, user_id: UUID) -> int:
    participants = (
        db.query(ConversationParticipant)
        .filter(ConversationParticipant.user_id == user_id)
        .all()
    )
    total = 0
    for p in participants:
        total += count_unread_messages(db, p.conversation_id, user_id, p.last_read_at)
    return total


def get_notification_history(
    db: Session,
    user_id: UUID,
    *,
    page: int = 1,
    page_size: int = 20,
):
    query = (
        db.query(ChatNotification)
        .filter(ChatNotification.user_id == user_id)
        .order_by(ChatNotification.created_at.desc())
    )
    return paginate_query(query, page, page_size)


def get_or_create_subscription(
    db: Session,
    user_id: UUID,
) -> ChatSubscription:
    sub = (
        db.query(ChatSubscription)
        .filter(ChatSubscription.user_id == user_id)
        .first()
    )
    if sub:
        today = date.today()
        if today > sub.period_end:
            sub.messages_used_this_month = 0
            sub.period_start = today.replace(day=1)
            if today.month == 12:
                sub.period_end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                sub.period_end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
            sub.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(sub)
        return sub

    today = date.today()
    period_start = today.replace(day=1)
    if today.month == 12:
        period_end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        period_end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)

    sub = ChatSubscription(
        user_id=user_id,
        period_start=period_start,
        period_end=period_end,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def increment_message_usage(db: Session, user_id: UUID) -> ChatSubscription:
    sub = get_or_create_subscription(db, user_id)
    sub.messages_used_this_month += 1
    sub.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(sub)
    return sub


def update_presence(
    db: Session,
    user_id: UUID,
    status: str,
) -> UserPresence:
    presence = (
        db.query(UserPresence)
        .filter(UserPresence.user_id == user_id)
        .first()
    )
    now = datetime.utcnow()
    if presence:
        presence.status = status
        presence.last_seen_at = now
        presence.updated_at = now
    else:
        presence = UserPresence(user_id=user_id, status=status, last_seen_at=now)
        db.add(presence)
    db.commit()
    db.refresh(presence)
    return presence


def get_online_users(db: Session) -> list[UserPresence]:
    cutoff = datetime.utcnow() - timedelta(minutes=5)
    return (
        db.query(UserPresence)
        .filter(
            UserPresence.status == "online",
            UserPresence.updated_at >= cutoff,
        )
        .all()
    )


def get_user_presence(db: Session, user_id: UUID) -> UserPresence | None:
    return (
        db.query(UserPresence)
        .filter(UserPresence.user_id == user_id)
        .first()
    )


def update_typing_indicator(
    db: Session,
    conversation_id: UUID,
    user_id: UUID,
    is_typing: bool,
) -> TypingIndicator:
    indicator = (
        db.query(TypingIndicator)
        .filter(
            TypingIndicator.conversation_id == conversation_id,
            TypingIndicator.user_id == user_id,
        )
        .first()
    )
    now = datetime.utcnow()
    if indicator:
        indicator.is_typing = is_typing
        indicator.updated_at = now
    else:
        indicator = TypingIndicator(
            conversation_id=conversation_id,
            user_id=user_id,
            is_typing=is_typing,
        )
        db.add(indicator)
    db.commit()
    db.refresh(indicator)
    return indicator


def get_typing_users(db: Session, conversation_id: UUID) -> list[TypingIndicator]:
    cutoff = datetime.utcnow() - timedelta(seconds=10)
    return (
        db.query(TypingIndicator)
        .filter(
            TypingIndicator.conversation_id == conversation_id,
            TypingIndicator.is_typing.is_(True),
            TypingIndicator.updated_at >= cutoff,
        )
        .all()
    )


def get_admin_dashboard_stats(db: Session) -> dict:
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "total_conversations": db.query(Conversation).filter(Conversation.is_deleted.is_(False)).count(),
        "active_conversations": db.query(Conversation).filter(
            Conversation.is_deleted.is_(False), Conversation.status == "open"
        ).count(),
        "closed_conversations": db.query(Conversation).filter(
            Conversation.is_deleted.is_(False), Conversation.status == "closed"
        ).count(),
        "archived_conversations": db.query(Conversation).filter(
            Conversation.is_deleted.is_(False), Conversation.status == "archived"
        ).count(),
        "total_messages_today": db.query(Message).filter(
            Message.created_at >= today_start, Message.is_deleted.is_(False)
        ).count(),
        "total_unread_messages": db.query(Message).filter(
            Message.is_deleted.is_(False),
            ~Message.id.in_(
                db.query(MessageReadReceipt.message_id)
            ),
        ).count(),
    }


def get_admin_conversations(
    db: Session,
    *,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
):
    query = db.query(Conversation)
    query = apply_soft_delete_filter(query, Conversation, False)
    if status:
        query = query.filter(Conversation.status == status)
    query = query.order_by(Conversation.updated_at.desc())
    return paginate_query(query, page, page_size)


def export_conversation_messages(db: Session, conversation_id: UUID) -> list[Message]:
    return (
        db.query(Message)
        .filter(
            Message.conversation_id == conversation_id,
            Message.is_deleted.is_(False),
        )
        .order_by(Message.created_at.asc())
        .all()
    )
