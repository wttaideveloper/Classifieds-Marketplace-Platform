from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.chat_schema import (
    ChatEligibilityResponse,
    ConversationNotificationsReadResponse,
    MonthlyLimitResponse,
    NotificationChannelsReference,
    NotificationPaginatedResponse,
    NotificationPreferenceResponse,
    NotificationPreferenceUpdate,
    NotificationReadAllResponse,
    NotificationResponse,
    TestPushRequest,
    TestPushResponse,
    UnreadCountResponse,
)
from app.services.chat_notification_service import (
    get_notification_channels_reference,
    get_preferences_service,
    mark_all_notifications_read_service,
    mark_conversation_notifications_read_service,
    mark_notification_read_service,
    notification_history_service,
    test_push_service,
    push_diagnostics_service,
    unread_count_service,
    update_preferences_service,
)
from app.services.chat_service import (
    chat_eligibility_service,
    monthly_limit_service,
    remaining_messages_service,
)

router = APIRouter(tags=["Chat Notifications"])

_NOTIFICATION_CHANNELS_DESCRIPTION = """
## Notification channels

| UI setting | API field | Provider | Client setup |
|------------|-----------|----------|--------------|
| Email Notifications | `email_enabled` | SMTP | Server-side email config |
| Push/App Notifications | `push_enabled` | **Firebase FCM** | `POST /api/v1/devices/register` with FCM token |
| SMS/Text Notifications | `sms_enabled` + `sms_phone_number` | **Bravo SMS** | E.164 phone e.g. `+15551234567` |
| In-app badge/history | `in_app_enabled` | Platform | `GET /notifications/unread-count` |

On new chat messages the backend dispatches to enabled channels automatically.
See `GET /notifications/channels` for a machine-readable reference.
"""


@router.get(
    "/channels",
    response_model=NotificationChannelsReference,
    summary="Notification Channel Reference",
    description=(
        "Lists notification channels, preference fields, and delivery providers "
        "(Firebase FCM for push, Bravo for SMS). No authentication required beyond "
        "standard API access — useful for frontend settings screens and Swagger."
    ),
)
def get_notification_channels():
    return get_notification_channels_reference()


@router.post(
    "/test-push",
    response_model=TestPushResponse,
    summary="Send Test Push Notification",
    description=(
        "Sends a Firebase FCM test notification to the authenticated user's registered device(s). "
        "Register first via `POST /devices/register`. "
        "FCM `data` uses camelCase keys (`conversationId`, `type=chat_message`) for mobile tap navigation. "
        "Requires Firebase service account credentials on the server."
    ),
)
def send_test_push(
    payload: TestPushRequest = Body(
        ...,
        openapi_examples={
            "default": {
                "summary": "Test chat message push",
                "value": {
                    "title": "New message",
                    "body": "Test push from backend",
                    "conversation_id": "7d365b31-9922-4219-92f3-8254d3d6e2e5",
                },
            },
            "specific_token": {
                "summary": "Target one registered device",
                "value": {
                    "token": "cnLJ9RUyS6GEafhAN_qkV3:APA91bH...",
                    "title": "New message",
                    "body": "Test push from backend",
                    "conversation_id": "7d365b31-9922-4219-92f3-8254d3d6e2e5",
                },
            },
        },
    ),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return test_push_service(db, current_user, payload)


@router.get(
    "/push-diagnostics",
    summary="Push Notification Diagnostics",
    description=(
        "Checks Firebase credential loading/initialization and how many device tokens "
        "are registered for the authenticated user. Use before POST /notifications/test-push."
    ),
)
def push_diagnostics(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return push_diagnostics_service(db, current_user)


@router.get(
    "/unread-count",
    response_model=UnreadCountResponse,
    summary="Get Unread Count",
    description=(
        "Returns unread message and in-app notification counts for the badge. "
        "Does not reflect Firebase or Bravo delivery status."
    ),
)
def get_unread_count(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return unread_count_service(db, current_user)


@router.get(
    "/preferences",
    response_model=NotificationPreferenceResponse,
    summary="Get Notification Preferences",
    description=(
        _NOTIFICATION_CHANNELS_DESCRIPTION
        + "\nReturns the current user's channel toggles and `sms_phone_number` for Bravo."
    ),
)
def get_notification_preferences(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return get_preferences_service(db, current_user)


@router.put(
    "/preferences",
    response_model=NotificationPreferenceResponse,
    summary="Update Notification Preferences",
    description=(
        _NOTIFICATION_CHANNELS_DESCRIPTION
        + "\nSend only fields you want to change. "
        "When enabling SMS, include `sms_phone_number` in E.164 format for Bravo delivery."
    ),
)
def update_notification_preferences(
    payload: NotificationPreferenceUpdate = Body(
        ...,
        openapi_examples={
            "firebase_push": {
                "summary": "Enable Firebase push + in-app",
                "description": "Register FCM token first via POST /devices/register.",
                "value": {
                    "push_enabled": True,
                    "in_app_enabled": True,
                    "sms_enabled": False,
                },
            },
            "bravo_sms": {
                "summary": "Enable Bravo SMS",
                "description": "Requires sms_phone_number in E.164 format.",
                "value": {
                    "sms_enabled": True,
                    "sms_phone_number": "+15551234567",
                },
            },
            "all_except_sms": {
                "summary": "Email + Firebase push, no SMS",
                "value": {
                    "email_enabled": True,
                    "push_enabled": True,
                    "sms_enabled": False,
                    "in_app_enabled": True,
                },
            },
        },
    ),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return update_preferences_service(db, current_user, payload)


@router.get(
    "/history",
    response_model=NotificationPaginatedResponse,
    summary="Get Notification History",
)
def get_notification_history(
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return notification_history_service(db, current_user, page=page, page_size=page_size)


@router.patch(
    "/read-all",
    response_model=NotificationReadAllResponse,
    summary="Mark All Notifications Read",
    description="Mark every unread notification as read for the authenticated user.",
)
def mark_all_notifications_read(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return mark_all_notifications_read_service(db, current_user)


@router.patch(
    "/conversation/{conversation_id}/read",
    response_model=ConversationNotificationsReadResponse,
    summary="Mark Conversation Notifications Read",
    description=(
        "Mark all unread chat notifications for a conversation as read. "
        "Also called automatically by `PATCH /conversations/{id}/read`."
    ),
)
def mark_conversation_notifications_read(
    conversation_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return mark_conversation_notifications_read_service(db, current_user, conversation_id)


@router.patch(
    "/{notification_id}/read",
    response_model=NotificationResponse,
    summary="Mark Notification Read",
    description="Mark a single notification as read. Returns the updated notification.",
)
def mark_notification_read(
    notification_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return mark_notification_read_service(db, current_user, notification_id)
