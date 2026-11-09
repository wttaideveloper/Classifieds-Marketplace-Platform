"""Event Discussions (docs/event-discussions-api.md). Mounted at /events/{event_id}/discussion, plus /event-discussions for the lists
that span events. Everything needs a signed-in user; what the caller may do depends on who they are for that event."""
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query, Request, status
from sqlalchemy.orm import Session

from app.core.dependencies import extract_access_token, get_current_user
from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.event_discussion_schema import (
    AppealRequest, DecisionRequest, DiscussionAccess, EventActivityPage, HistoryPage, MessageCreate, MessageDetail, MessageEdit, MessagePage,
    MessageResult, MessageStatus, ModerationPage,
)
from app.services import event_discussion_service as svc

router = APIRouter(tags=["Event Discussions"])
overview_router = APIRouter(tags=["Event Discussions"])

PAGE = Query(DEFAULT_PAGE, ge=1)
PAGE_SIZE = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
EVENT = Path(..., description="Event ID")


def _t(request: Request) -> str | None:
    return extract_access_token(request)


@router.get("/access", response_model=DiscussionAccess, summary="Can I open this event's discussion?",
            description="Call it when the Event Details screen opens. `can_read: false` means the Discussion tab stays locked, with `locked_reason` and a `message` to show: "
                        "`NOT_REGISTERED`, `REGISTRATION_CANCELLED` (access is revoked) or `NOT_EVENT_ADMIN`. A Super Admin gets `role: super_admin` with `can_post: false`.")
def access(request: Request, event_id: UUID = EVENT, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.access_for(db, event_id, user, _t(request))


@router.get("/messages", response_model=MessagePage, summary="The discussion's messages",
            description="Top-level messages (or the replies of `parent_id`). Announcements first, then by `order` (`oldest` | `newest`). Everyone sees published messages; you also see your own that "
                        "are being checked, pending review, blocked or deleted, with `status` and `status_message`. 403 `DISCUSSION_LOCKED` when you may not read.")
def list_messages(request: Request, event_id: UUID = EVENT, parent_id: UUID | None = Query(None), order: str = Query("oldest", pattern="^(oldest|newest)$"),
                  page: int = PAGE, page_size: int = PAGE_SIZE, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.list_messages(db, event_id, user, _t(request), parent_id=parent_id, order=order, page=page, page_size=page_size)


@router.post("/messages", response_model=MessageResult, status_code=status.HTTP_201_CREATED, summary="Post a message or a reply",
             description=(
                 "The message is checked by AI before anyone else can see it. The answer tells you the outcome: `message.status` is `published` (safe), `pending_review` (flagged: hidden until an "
                 "Enterprise Admin decides; show `user_message`), `blocked` (extreme: not posted; show `user_message`, the sender may edit and try again) or `pending_moderation` (the AI was unavailable: "
                 "unpublished until it can be checked). Plain text only, up to 2000 characters. Enterprise Admins' messages are checked too; only they can set `is_announcement`. "
                 "403 `SUPER_ADMIN_READ_ONLY`, 403 `DISCUSSION_LOCKED`, 409 `DISCUSSION_CLOSED`, 409 `PARENT_NOT_AVAILABLE`, 409 `DUPLICATE_MESSAGE`."
             ))
def post_message(request: Request, event_id: UUID = EVENT, payload: MessageCreate = Body(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.post_message(db, event_id, user, _t(request), body=payload.body, parent_id=payload.parent_id, is_announcement=payload.is_announcement)


@router.get("/messages/mine", response_model=MessagePage, summary="My own messages and their status",
            description="Newest first, `status` filters. Use it for 'the status of my flagged messages'.")
def my_messages(request: Request, event_id: UUID = EVENT, status_filter: MessageStatus | None = Query(None, alias="status"), page: int = PAGE, page_size: int = PAGE_SIZE,
                db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.my_messages(db, event_id, user, _t(request), status=status_filter, page=page, page_size=page_size)


@router.get("/messages/{message_id}", response_model=MessageDetail, summary="One message with its replies",
            description="The target of every notification. Opening it marks a reply to you, and the notifications about it, as read (which ends the unread-reply nudge). "
                        "404 `MESSAGE_UNAVAILABLE` ('This message is no longer available.') when it was deleted, never published, or is not yours to see; "
                        "its author still sees it with its `status`.")
def get_message(request: Request, message_id: UUID, event_id: UUID = EVENT, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.get_message(db, event_id, message_id, user, _t(request))


@router.put("/messages/{message_id}", response_model=MessageResult, summary="Edit a blocked message and send it again",
            description="Only the author, and only while it is `blocked` (or still `pending_moderation`). It goes through moderation again.")
def edit_message(request: Request, message_id: UUID, event_id: UUID = EVENT, payload: MessageEdit = Body(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.edit_message(db, event_id, message_id, user, _t(request), body=payload.body)


@router.post("/messages/{message_id}/appeal", response_model=MessageResult, summary="Ask for a blocked message to be reviewed",
             description="For a message the AI blocked by mistake. It goes to the event's Enterprise Admins as `pending_review` with `is_appeal: true`. One appeal per message (409 `ALREADY_APPEALED`).")
def appeal_message(request: Request, message_id: UUID, event_id: UUID = EVENT, payload: AppealRequest = Body(default_factory=AppealRequest), db: Session = Depends(get_db),
                   user: dict = Depends(get_current_user)):
    return svc.appeal_message(db, event_id, message_id, user, _t(request), note=payload.note)


@router.post("/messages/{message_id}/decision", response_model=MessageResult, summary="Approve or delete a flagged message (Enterprise Admin)",
             description="Only the Enterprise Admin of the event, for a message that is `pending_review`. `approve` publishes it and tells the sender; `delete` means it is never published and the sender is told. "
                         "You cannot approve your own message (403 `SELF_REVIEW`). A Super Admin gets 403 `SUPER_ADMIN_READ_ONLY`.")
def decide(request: Request, message_id: UUID, event_id: UUID = EVENT, payload: DecisionRequest = Body(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.decide(db, event_id, message_id, user, _t(request), action=payload.action, note=payload.note)


@router.get("/moderation/queue", response_model=ModerationPage, summary="Flagged messages of this event (Enterprise Admin; Super Admin read only)",
            description="`status` defaults to `pending_review`; use `blocked`, `published` or `deleted` to look at the others. Each item has the event name, sender, content, time, the AI's reason and status.")
def event_queue(request: Request, event_id: UUID = EVENT, status_filter: MessageStatus = Query("pending_review", alias="status"), page: int = PAGE, page_size: int = PAGE_SIZE,
                db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.event_queue(db, event_id, user, _t(request), status=status_filter, page=page, page_size=page_size)


@router.get("/moderation/history", response_model=HistoryPage, summary="Moderation history of this event (Enterprise Admin; Super Admin read only)",
            description="Every step: submitted, auto_published, flagged, blocked, ai_unavailable, approved, deleted, edited_resubmitted, appealed. Who or what (actor_role `system` is the AI), the result, the reason and the time. Newest first.")
def history(request: Request, event_id: UUID = EVENT, action: str | None = Query(None), message_id: UUID | None = Query(None), page: int = PAGE, page_size: int = PAGE_SIZE,
            db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.history(db, event_id, user, _t(request), action=action, message_id=message_id, page=page, page_size=page_size)


# ---------------------------------------------------------------- across events

@overview_router.get("/flagged", response_model=ModerationPage, summary="Flagged messages across my events (Enterprise Admin; Super Admin read only)",
                     description="The review screen's list: every flagged message of the events the caller manages, or (Super Admin) of every enterprise, newest first. `event_id` narrows it; `tenant_id` is Super Admin only.")
def flagged(request: Request, event_id: UUID | None = Query(None), tenant_id: UUID | None = Query(None), status_filter: MessageStatus = Query("pending_review", alias="status"),
            page: int = PAGE, page_size: int = PAGE_SIZE, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.tenant_queue(db, user, _t(request), event_id=event_id, tenant_id=tenant_id, status=status_filter, page=page, page_size=page_size)


@overview_router.get("/activity", response_model=EventActivityPage, summary="Discussion activity per event (Enterprise Admin; Super Admin read only)",
                     description="One row per event that has messages, with counts by status and the last activity. A Super Admin sees every enterprise (`tenant_id` narrows) and then opens an event's discussion, queue and history read only.")
def activity(request: Request, tenant_id: UUID | None = Query(None), page: int = PAGE, page_size: int = PAGE_SIZE, db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    return svc.activity(db, user, _t(request), tenant_id=tenant_id, page=page, page_size=page_size)
