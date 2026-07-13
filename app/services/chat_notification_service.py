from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.repository import chat_repo as chat_repo
from app.repository.query_utils import build_pagination_meta
from app.services.bravo_sms_service import send_sms as send_bravo_sms
from app.services.firebase_push_service import send_push_to_tokens
from app.schemas.chat_schema import (
    ConversationNotificationsReadResponse,
    NotificationChannelInfo,
    NotificationChannelsReference,
    NotificationPaginatedResponse,
    NotificationPreferenceResponse,
    NotificationPreferenceUpdate,
    NotificationReadAllResponse,
    NotificationResponse,
    UnreadCountResponse,
)


def _parse_user_id(current_user: dict) -> UUID:
    return UUID(str(current_user["id"]))


def register_device_service(db: Session, current_user: dict, token: str, platform: str):
    user_id = _parse_user_id(current_user)
    device = chat_repo.register_device_token(db, user_id, token, platform)
    return {
        "id": device.id,
        "token": device.token,
        "platform": device.platform,
        "is_active": device.is_active,
        "created_at": device.created_at,
    }


def remove_device_service(db: Session, current_user: dict, token: str):
    user_id = _parse_user_id(current_user)
    removed = chat_repo.deactivate_device_token(db, user_id, token)
    if not removed:
        raise HTTPException(status_code=404, detail="Device token not found")
    return {"message": "Device token removed successfully"}


def get_notification_channels_reference():
    return NotificationChannelsReference(
        channels=[
            NotificationChannelInfo(
                channel="Email Notifications",
                preference_field="email_enabled",
                provider="SMTP",
                setup="Uses server email configuration.",
            ),
            NotificationChannelInfo(
                channel="Push/App Notifications",
                preference_field="push_enabled",
                provider="Firebase Cloud Messaging (FCM)",
                setup="Register FCM token via POST /api/v1/devices/register.",
            ),
            NotificationChannelInfo(
                channel="SMS/Text Notifications",
                preference_field="sms_enabled + sms_phone_number",
                provider="Bravo SMS",
                setup="Set sms_phone_number in E.164 format (e.g. +15551234567) when enabling SMS.",
            ),
            NotificationChannelInfo(
                channel="In-app Notifications",
                preference_field="in_app_enabled",
                provider="Platform",
                setup="Badge count via GET /notifications/unread-count and history via GET /notifications/history.",
            ),
        ]
    )


def get_preferences_service(db: Session, current_user: dict):
    user_id = _parse_user_id(current_user)
    prefs = chat_repo.get_notification_preferences(db, user_id)
    return NotificationPreferenceResponse.model_validate(prefs)


def update_preferences_service(
    db: Session,
    current_user: dict,
    data: NotificationPreferenceUpdate,
):
    user_id = _parse_user_id(current_user)
    updates = data.model_dump(exclude_unset=True)
    prefs = chat_repo.update_notification_preferences(db, user_id, updates)
    return NotificationPreferenceResponse.model_validate(prefs)


def unread_count_service(db: Session, current_user: dict):
    user_id = _parse_user_id(current_user)
    unread_messages = chat_repo.count_total_unread_messages(db, user_id)
    unread_notifications = chat_repo.count_unread_notifications(db, user_id)
    return UnreadCountResponse(
        unread_messages=unread_messages,
        unread_notifications=unread_notifications,
        total_unread=unread_messages + unread_notifications,
    )


def notification_history_service(
    db: Session,
    current_user: dict,
    *,
    page: int = 1,
    page_size: int = 20,
):
    user_id = _parse_user_id(current_user)
    items, total = chat_repo.get_notification_history(
        db, user_id, page=page, page_size=page_size
    )

    return NotificationPaginatedResponse(
        items=[NotificationResponse.model_validate(item) for item in items],
        pagination=build_pagination_meta(total, page, page_size),
    )


def create_message_notifications(
    db: Session,
    *,
    conversation_id: UUID,
    sender_id: UUID,
    message_id: UUID,
    title: str,
    body: str,
) -> None:
    conversation = chat_repo.get_conversation_by_id(db, conversation_id, with_participants=True)
    if not conversation:
        return

    for participant in conversation.participants:
        if participant.user_id == sender_id:
            continue

        prefs = chat_repo.get_notification_preferences(db, participant.user_id)

        if prefs.in_app_enabled:
            chat_repo.create_notification(
                db,
                user_id=participant.user_id,
                notification_type="chat_message",
                title=title,
                body=body,
                data={
                    "conversation_id": str(conversation_id),
                    "message_id": str(message_id),
                },
            )

        if prefs.push_enabled:
            _dispatch_push_notification(db, participant.user_id, title, body, conversation_id, message_id)

        if prefs.email_enabled:
            _dispatch_email_notification(participant.user_id, title, body, conversation_id)

        if prefs.sms_enabled:
            _dispatch_sms_notification(db, participant.user_id, title, body, conversation_id)


def _dispatch_push_notification(
    db: Session,
    user_id: UUID,
    title: str,
    body: str,
    conversation_id: UUID,
    message_id: UUID,
) -> None:
    tokens = [device.token for device in chat_repo.get_active_device_tokens(db, user_id)]
    if not tokens:
        return
    send_push_to_tokens(
        tokens,
        title=title,
        body=body,
        data={
            "type": "chat_message",
            "conversation_id": str(conversation_id),
            "message_id": str(message_id),
        },
    )


def _dispatch_email_notification(
    user_id: UUID,
    title: str,
    body: str,
    conversation_id: UUID,
) -> None:
    """Send email when SMTP delivery is configured for chat alerts."""
    # Email delivery can be wired here using settings.email_user / email_pass.


def _dispatch_sms_notification(
    db: Session,
    user_id: UUID,
    title: str,
    body: str,
    conversation_id: UUID,
) -> None:
    prefs = chat_repo.get_notification_preferences(db, user_id)
    if not prefs.sms_phone_number:
        return
    message = f"{title}: {body}" if title else body
    send_bravo_sms(
        phone=prefs.sms_phone_number,
        message=message,
        reference_id=str(user_id),
    )


def mark_notification_read_service(db: Session, current_user: dict, notification_id: UUID):
    user_id = _parse_user_id(current_user)
    notification = chat_repo.mark_notification_read(db, notification_id, user_id)
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    return NotificationResponse.model_validate(notification)


def mark_all_notifications_read_service(db: Session, current_user: dict):
    user_id = _parse_user_id(current_user)
    marked = chat_repo.mark_all_notifications_read(db, user_id)
    return NotificationReadAllResponse(marked_read=marked)


def mark_conversation_notifications_read_service(
    db: Session,
    current_user: dict,
    conversation_id: UUID,
):
    user_id = _parse_user_id(current_user)
    if not chat_repo.is_participant(db, conversation_id, user_id):
        raise HTTPException(status_code=403, detail="Not authorized")
    marked = chat_repo.mark_conversation_notifications_read(db, user_id, conversation_id)
    return ConversationNotificationsReadResponse(
        conversation_id=conversation_id,
        marked_read=marked,
    )
