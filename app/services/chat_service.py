from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.chat_model import Conversation, ConversationParticipant
from app.repository import chat_repo as chat_repo
from app.repository.query_utils import build_pagination_meta
from app.schemas.chat_schema import (
    ChatEligibilityResponse,
    ChatExportResponse,
    ChatDashboardResponse,
    ConversationCreate,
    ConversationListItemResponse,
    ConversationPaginatedResponse,
    ConversationResponse,
    ConversationStatusUpdateResponse,
    CursorPaginatedResponse,
    CursorPaginationMeta,
    MessageCreate,
    MessageDeleteResponse,
    MessageReadResponse,
    MessageReadStatusResponse,
    MessageResponse,
    MonthlyLimitResponse,
    OnlineUsersResponse,
    ParticipantResponse,
    PresenceResponse,
    ProviderAssignmentResponse,
    TypingIndicatorResponse,
)


def _parse_user_id(current_user: dict) -> UUID:
    return UUID(str(current_user["id"]))


def _map_conversation_detail(db: Session, conversation: Conversation, user_id: UUID) -> dict:
    participant = chat_repo.get_participant(db, conversation.id, user_id)
    unread = chat_repo.count_unread_messages(
        db, conversation.id, user_id, participant.last_read_at if participant else None
    )
    return {
        "id": conversation.id,
        "tenant_id": conversation.tenant_id,
        "status": conversation.status,
        "conversation_type": conversation.conversation_type,
        "is_read_only": conversation.is_read_only,
        "subject": conversation.subject,
        "context_type": conversation.context_type,
        "context_id": conversation.context_id,
        "assigned_provider_id": conversation.assigned_provider_id,
        "expires_at": conversation.expires_at,
        "last_message_at": conversation.last_message_at,
        "last_message_preview": conversation.last_message_preview,
        "created_by": conversation.created_by,
        "unread_count": unread,
        "participants": [
            ParticipantResponse.model_validate(p).model_dump()
            for p in conversation.participants
        ],
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
    }


def _map_conversation_list_item(db: Session, conversation: Conversation, user_id: UUID) -> dict:
    participant = chat_repo.get_participant(db, conversation.id, user_id)
    unread = chat_repo.count_unread_messages(
        db, conversation.id, user_id, participant.last_read_at if participant else None
    )
    return {
        "id": conversation.id,
        "status": conversation.status,
        "conversation_type": conversation.conversation_type,
        "subject": conversation.subject,
        "last_message_at": conversation.last_message_at,
        "last_message_preview": conversation.last_message_preview,
        "unread_count": unread,
        "assigned_provider_id": conversation.assigned_provider_id,
        "updated_at": conversation.updated_at,
    }


def _validate_chat_rules(conversation: Conversation) -> None:
    if conversation.expires_at and conversation.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Conversation has expired")
    if conversation.status != "open":
        raise HTTPException(
            status_code=400,
            detail=f"Conversation is {conversation.status}. Cannot send messages.",
        )
    if conversation.is_read_only:
        raise HTTPException(status_code=400, detail="Conversation is read-only")


def _check_subscription_eligibility(db: Session, user_id: UUID, conversation: Conversation) -> None:
    if conversation.conversation_type != "preview":
        return

    sub = chat_repo.get_or_create_subscription(db, user_id)
    remaining = sub.monthly_message_limit - sub.messages_used_this_month
    if remaining <= 0:
        raise HTTPException(
            status_code=403,
            detail="Monthly free message limit reached. Upgrade your subscription to continue.",
        )


def create_conversation_service(db: Session, current_user: dict, data: ConversationCreate):
    user_id = _parse_user_id(current_user)
    participant_ids = [p.user_id for p in data.participant_ids]

    existing = chat_repo.find_existing_conversation(
        db,
        user_id,
        participant_ids,
        context_type=data.context_type,
        context_id=data.context_id,
    )
    if existing:
        return ConversationResponse.model_validate(
            _map_conversation_detail(db, existing, user_id)
        )

    conversation = Conversation(
        tenant_id=data.tenant_id,
        subject=data.subject,
        conversation_type=data.conversation_type,
        context_type=data.context_type,
        context_id=data.context_id,
        created_by=user_id,
        assigned_provider_id=data.provider_id,
    )

    if data.conversation_type == "preview":
        from datetime import timedelta
        conversation.expires_at = datetime.utcnow() + timedelta(days=7)

    participants = [
        ConversationParticipant(user_id=user_id, role="customer"),
    ]
    for p in data.participant_ids:
        participants.append(ConversationParticipant(user_id=p.user_id, role=p.role))

    conversation.participants = participants
    conversation = chat_repo.create_conversation(db, conversation)

    if data.provider_id:
        chat_repo.assign_provider(db, conversation.id, data.provider_id, user_id)

    conversation = chat_repo.get_conversation_by_id(db, conversation.id, with_participants=True)
    return ConversationResponse.model_validate(
        _map_conversation_detail(db, conversation, user_id)
    )


def list_conversations_service(
    db: Session,
    current_user: dict,
    *,
    status_filter: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
):
    user_id = _parse_user_id(current_user)
    items, total = chat_repo.get_user_conversations(
        db, user_id, status=status_filter, search=search, page=page, page_size=page_size
    )
    return ConversationPaginatedResponse(
        items=[
            ConversationListItemResponse.model_validate(
                _map_conversation_list_item(db, item, user_id)
            )
            for item in items
        ],
        pagination=build_pagination_meta(total, page, page_size),
    )


def list_provider_conversations_service(
    db: Session,
    current_user: dict,
    *,
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = 20,
):
    user_id = _parse_user_id(current_user)
    items, total = chat_repo.get_provider_conversations(
        db, user_id, status=status_filter, page=page, page_size=page_size
    )
    return ConversationPaginatedResponse(
        items=[
            ConversationListItemResponse.model_validate(
                _map_conversation_list_item(db, item, user_id)
            )
            for item in items
        ],
        pagination=build_pagination_meta(total, page, page_size),
    )


def get_conversation_service(db: Session, current_user: dict, conversation_id: UUID):
    user_id = _parse_user_id(current_user)
    conversation = chat_repo.get_conversation_by_id(db, conversation_id, with_participants=True)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not chat_repo.is_participant(db, conversation_id, user_id):
        raise HTTPException(status_code=403, detail="Not authorized to view this conversation")
    return ConversationResponse.model_validate(
        _map_conversation_detail(db, conversation, user_id)
    )


def _update_conversation_status(
    db: Session,
    current_user: dict,
    conversation_id: UUID,
    new_status: str,
) -> ConversationStatusUpdateResponse:
    user_id = _parse_user_id(current_user)
    conversation = chat_repo.get_conversation_by_id(db, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not chat_repo.is_participant(db, conversation_id, user_id):
        raise HTTPException(status_code=403, detail="Not authorized")

    conversation.status = new_status
    conversation = chat_repo.save_conversation(db, conversation)
    return ConversationStatusUpdateResponse(
        id=conversation.id,
        status=conversation.status,
        updated_at=conversation.updated_at,
    )


def close_conversation_service(db: Session, current_user: dict, conversation_id: UUID):
    return _update_conversation_status(db, current_user, conversation_id, "closed")


def reopen_conversation_service(db: Session, current_user: dict, conversation_id: UUID):
    return _update_conversation_status(db, current_user, conversation_id, "open")


def archive_conversation_service(db: Session, current_user: dict, conversation_id: UUID):
    return _update_conversation_status(db, current_user, conversation_id, "archived")


def search_conversations_service(
    db: Session,
    current_user: dict,
    *,
    search: str,
    provider_id: UUID | None = None,
    page: int = 1,
    page_size: int = 20,
):
    user_id = _parse_user_id(current_user)
    items, total = chat_repo.search_conversations(
        db, user_id, search=search, provider_id=provider_id, page=page, page_size=page_size
    )
    return ConversationPaginatedResponse(
        items=[
            ConversationListItemResponse.model_validate(
                _map_conversation_list_item(db, item, user_id)
            )
            for item in items
        ],
        pagination=build_pagination_meta(total, page, page_size),
    )


def send_message_service(db: Session, current_user: dict, data: MessageCreate):
    user_id = _parse_user_id(current_user)
    conversation = chat_repo.get_conversation_by_id(db, data.conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not chat_repo.is_participant(db, data.conversation_id, user_id):
        raise HTTPException(status_code=403, detail="Not authorized to send messages")

    _validate_chat_rules(conversation)
    _check_subscription_eligibility(db, user_id, conversation)

    if data.conversation_id and data.attachment_id:
        attachment = chat_repo.get_attachment_by_id(db, data.attachment_id)
        if not attachment or attachment.conversation_id != data.conversation_id:
            raise HTTPException(status_code=400, detail="Invalid attachment for this conversation")

    from app.models.chat_model import Message

    message = Message(
        conversation_id=data.conversation_id,
        sender_id=user_id,
        content=data.content,
        message_type=data.message_type,
        attachment_id=data.attachment_id,
    )
    message = chat_repo.create_message(db, message)

    if data.attachment_id:
        attachment = chat_repo.get_attachment_by_id(db, data.attachment_id)
        if attachment:
            attachment.message_id = message.id
            db.commit()

    preview = chat_repo.message_list_preview(message)
    conversation.last_message_at = message.created_at
    conversation.last_message_preview = preview
    chat_repo.save_conversation(db, conversation)

    if conversation.conversation_type == "preview":
        chat_repo.increment_message_usage(db, user_id)

    return _map_message(db, message)


def get_messages_service(
    db: Session,
    current_user: dict,
    conversation_id: UUID,
    *,
    cursor: str | None = None,
    limit: int = 50,
):
    user_id = _parse_user_id(current_user)
    if not chat_repo.is_participant(db, conversation_id, user_id):
        raise HTTPException(status_code=403, detail="Not authorized")

    items, has_more, next_cursor = chat_repo.get_conversation_messages(
        db, conversation_id, cursor=cursor, limit=limit
    )
    return CursorPaginatedResponse(
        items=[MessageResponse.model_validate(_map_message(db, m)) for m in items],
        pagination=CursorPaginationMeta(
            has_more=has_more,
            next_cursor=next_cursor,
            limit=limit,
        ),
    )


def _map_message(db: Session, message) -> dict:
    receipts = chat_repo.get_message_read_receipts(db, message.id)
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "sender_id": message.sender_id,
        "content": message.content,
        "message_type": message.message_type,
        "attachment_id": message.attachment_id,
        "is_deleted": message.is_deleted,
        "created_at": message.created_at,
        "read_by": [r.user_id for r in receipts],
    }


def mark_message_read_service(db: Session, current_user: dict, message_id: UUID):
    user_id = _parse_user_id(current_user)
    message = chat_repo.get_message_by_id(db, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    if not chat_repo.is_participant(db, message.conversation_id, user_id):
        raise HTTPException(status_code=403, detail="Not authorized")

    receipt = chat_repo.mark_message_read(db, message_id, user_id)
    return MessageReadResponse(
        message_id=receipt.message_id,
        user_id=receipt.user_id,
        read_at=receipt.read_at,
    )


def mark_conversation_read_service(db: Session, current_user: dict, conversation_id: UUID):
    user_id = _parse_user_id(current_user)
    if not chat_repo.is_participant(db, conversation_id, user_id):
        raise HTTPException(status_code=403, detail="Not authorized")
    chat_repo.mark_conversation_read(db, conversation_id, user_id)
    return {"conversation_id": conversation_id, "read_at": datetime.utcnow()}


def get_read_status_service(db: Session, current_user: dict, message_id: UUID):
    user_id = _parse_user_id(current_user)
    message = chat_repo.get_message_by_id(db, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    if not chat_repo.is_participant(db, message.conversation_id, user_id):
        raise HTTPException(status_code=403, detail="Not authorized")

    receipts = chat_repo.get_message_read_receipts(db, message_id)
    conversation = chat_repo.get_conversation_by_id(db, message.conversation_id, with_participants=True)
    total_recipients = len(conversation.participants) - 1 if conversation else 0

    return MessageReadStatusResponse(
        message_id=message_id,
        total_recipients=max(total_recipients, 0),
        read_count=len(receipts),
        read_by=[
            MessageReadResponse(message_id=r.message_id, user_id=r.user_id, read_at=r.read_at)
            for r in receipts
        ],
    )


def delete_message_service(db: Session, current_user: dict, message_id: UUID):
    user_id = _parse_user_id(current_user)
    message = chat_repo.get_message_by_id(db, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.sender_id != user_id and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to delete this message")

    message = chat_repo.soft_delete_message(db, message)
    chat_repo.sync_conversation_last_message(db, message.conversation_id)
    return MessageDeleteResponse(
        id=message.id,
        is_deleted=message.is_deleted,
        deleted_at=message.deleted_at,
    )


def search_messages_service(
    db: Session,
    current_user: dict,
    *,
    search: str,
    conversation_id: UUID | None = None,
    provider_id: UUID | None = None,
    page: int = 1,
    page_size: int = 20,
):
    user_id = _parse_user_id(current_user)
    items, total = chat_repo.search_messages(
        db,
        user_id,
        search=search,
        conversation_id=conversation_id,
        provider_id=provider_id,
        page=page,
        page_size=page_size,
    )
    from app.schemas.chat_schema import MessageResponse
    from app.schemas.common_schema import PaginatedResponse

    return PaginatedResponse(
        items=[MessageResponse.model_validate(_map_message(db, m)) for m in items],
        pagination=build_pagination_meta(total, page, page_size),
    )


def assign_provider_service(db: Session, current_user: dict, conversation_id: UUID, provider_id: UUID):
    user_id = _parse_user_id(current_user)
    conversation = chat_repo.get_conversation_by_id(db, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    assignment = chat_repo.assign_provider(db, conversation_id, provider_id, user_id)
    conversation.assigned_provider_id = provider_id
    chat_repo.save_conversation(db, conversation)

    existing_provider = chat_repo.get_participant(db, conversation_id, provider_id)
    if not existing_provider:
        db.add(ConversationParticipant(
            conversation_id=conversation_id,
            user_id=provider_id,
            role="provider",
        ))
        db.commit()

    return ProviderAssignmentResponse.model_validate(assignment)


def get_assigned_provider_service(db: Session, current_user: dict, conversation_id: UUID):
    user_id = _parse_user_id(current_user)
    if not chat_repo.is_participant(db, conversation_id, user_id):
        raise HTTPException(status_code=403, detail="Not authorized")

    assignment = chat_repo.get_active_provider_assignment(db, conversation_id)
    if not assignment:
        raise HTTPException(status_code=404, detail="No provider assigned")
    return ProviderAssignmentResponse.model_validate(assignment)


def chat_eligibility_service(db: Session, current_user: dict):
    user_id = _parse_user_id(current_user)
    sub = chat_repo.get_or_create_subscription(db, user_id)
    remaining = sub.monthly_message_limit - sub.messages_used_this_month
    eligible = sub.is_active and remaining > 0

    return ChatEligibilityResponse(
        eligible=eligible,
        plan_type=sub.plan_type,
        reason=None if eligible else "Monthly message limit reached",
        remaining_messages=max(remaining, 0),
        monthly_limit=sub.monthly_message_limit,
        messages_used=sub.messages_used_this_month,
        period_end=sub.period_end,
    )


def remaining_messages_service(db: Session, current_user: dict):
    user_id = _parse_user_id(current_user)
    sub = chat_repo.get_or_create_subscription(db, user_id)
    remaining = max(sub.monthly_message_limit - sub.messages_used_this_month, 0)
    return {
        "remaining_messages": remaining,
        "monthly_limit": sub.monthly_message_limit,
        "messages_used": sub.messages_used_this_month,
    }


def monthly_limit_service(db: Session, current_user: dict):
    user_id = _parse_user_id(current_user)
    sub = chat_repo.get_or_create_subscription(db, user_id)
    remaining = max(sub.monthly_message_limit - sub.messages_used_this_month, 0)
    return MonthlyLimitResponse(
        monthly_limit=sub.monthly_message_limit,
        messages_used=sub.messages_used_this_month,
        remaining_messages=remaining,
        period_start=sub.period_start,
        period_end=sub.period_end,
    )


def update_presence_service(db: Session, current_user: dict, status: str):
    user_id = _parse_user_id(current_user)
    presence = chat_repo.update_presence(db, user_id, status)
    return PresenceResponse(
        user_id=presence.user_id,
        status=presence.status,
        last_seen_at=presence.last_seen_at,
    )


def get_online_users_service(db: Session):
    users = chat_repo.get_online_users(db)
    return OnlineUsersResponse(
        users=[
            PresenceResponse(user_id=u.user_id, status=u.status, last_seen_at=u.last_seen_at)
            for u in users
        ],
        total=len(users),
    )


def get_last_seen_service(db: Session, user_id: UUID):
    presence = chat_repo.get_user_presence(db, user_id)
    if not presence:
        raise HTTPException(status_code=404, detail="User presence not found")
    return PresenceResponse(
        user_id=presence.user_id,
        status=presence.status,
        last_seen_at=presence.last_seen_at,
    )


def update_typing_service(
    db: Session,
    current_user: dict,
    conversation_id: UUID,
    is_typing: bool,
):
    user_id = _parse_user_id(current_user)
    if not chat_repo.is_participant(db, conversation_id, user_id):
        raise HTTPException(status_code=403, detail="Not authorized")

    indicator = chat_repo.update_typing_indicator(db, conversation_id, user_id, is_typing)
    return TypingIndicatorResponse(
        conversation_id=indicator.conversation_id,
        user_id=indicator.user_id,
        is_typing=indicator.is_typing,
        updated_at=indicator.updated_at,
    )


def get_typing_service(db: Session, current_user: dict, conversation_id: UUID):
    user_id = _parse_user_id(current_user)
    if not chat_repo.is_participant(db, conversation_id, user_id):
        raise HTTPException(status_code=403, detail="Not authorized")

    indicators = chat_repo.get_typing_users(db, conversation_id)
    return [
        TypingIndicatorResponse(
            conversation_id=i.conversation_id,
            user_id=i.user_id,
            is_typing=i.is_typing,
            updated_at=i.updated_at,
        )
        for i in indicators
        if i.user_id != user_id
    ]


def admin_dashboard_service(db: Session):
    stats = chat_repo.get_admin_dashboard_stats(db)
    return ChatDashboardResponse(**stats)


def admin_list_conversations_service(
    db: Session,
    *,
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = 20,
):
    items, total = chat_repo.get_admin_conversations(
        db, status=status_filter, page=page, page_size=page_size
    )

    return ConversationPaginatedResponse(
        items=[
            ConversationListItemResponse.model_validate({
                "id": item.id,
                "status": item.status,
                "conversation_type": item.conversation_type,
                "subject": item.subject,
                "last_message_at": item.last_message_at,
                "last_message_preview": item.last_message_preview,
                "unread_count": 0,
                "assigned_provider_id": item.assigned_provider_id,
                "updated_at": item.updated_at,
            })
            for item in items
        ],
        pagination=build_pagination_meta(total, page, page_size),
    )


def export_conversation_service(db: Session, conversation_id: UUID):
    conversation = chat_repo.get_conversation_by_id(db, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = chat_repo.export_conversation_messages(db, conversation_id)
    return ChatExportResponse(
        conversation_id=conversation_id,
        exported_at=datetime.utcnow(),
        message_count=len(messages),
        messages=[MessageResponse.model_validate(_map_message(db, m)) for m in messages],
    )
