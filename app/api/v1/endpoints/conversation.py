import logging
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_admin, get_current_user
from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.chat_schema import (
    ConversationArchiveRequest,
    ConversationArchiveResponse,
    ConversationCreate,
    ConversationPaginatedResponse,
    ConversationResponse,
    ConversationStatusUpdateResponse,
    ProviderConversationPaginatedResponse,
)
from app.services.chat_service import (
    archive_conversation_service,
    close_conversation_service,
    create_conversation_service,
    get_conversation_service,
    list_conversations_service,
    list_provider_conversations_service,
    reopen_conversation_service,
    search_conversations_service,
)

router = APIRouter(tags=["Conversations"])
logger = logging.getLogger(__name__)


@router.post(
    "/",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create or Open Conversation",
    description=(
        "Create a new conversation or return an existing open conversation "
        "with the same participants and context. Supports preview and booking chat types."
    ),
)
def create_conversation(
    payload: ConversationCreate = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return create_conversation_service(db, current_user, payload)


@router.get(
    "/",
    response_model=ConversationPaginatedResponse,
    summary="List User Conversations",
    description=(
        "List conversations for the authenticated user. "
        "Use `status=archived` for archived chats, or call `GET /conversations/archived`."
    ),
)
def list_conversations(
    status_filter: str | None = Query(
        None,
        alias="status",
        description="Filter by status: open, closed, or archived.",
    ),
    search: str | None = Query(None, description="Search by subject or last message."),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return list_conversations_service(
        db, current_user, status_filter=status_filter, search=search, page=page, page_size=page_size
    )


@router.get(
    "/archived",
    response_model=ConversationPaginatedResponse,
    summary="List Archived Conversations",
    description="List archived conversations for the authenticated user (same as `GET /conversations?status=archived`).",
)
def list_archived_conversations(
    search: str | None = Query(None, description="Search by subject or last message."),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return list_conversations_service(
        db,
        current_user,
        status_filter="archived",
        search=search,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/provider",
    response_model=ProviderConversationPaginatedResponse,
    summary="List Provider Conversations",
    description=(
        "List conversations for the authenticated provider. "
        "Each item includes `other_participant_user_id` (customer/user/patient) "
        "for mapping to `GET /api/v1/presence/online`. "
        "Use `status=archived` for archived chats, or call `GET /conversations/provider/archived`."
    ),
)
def list_provider_conversations(
    status_filter: str | None = Query(
        None,
        alias="status",
        description="Filter by status: open, closed, or archived.",
    ),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    response = list_provider_conversations_service(
        db, current_user, status_filter=status_filter, page=page, page_size=page_size
    )
    # TEMPORARY DIAGNOSTIC — remove once the GET /conversations/provider
    # empty-result investigation is closed. No tokens/headers/PII: resolved
    # identity, requested pagination, and the final returned count only.
    # Never allowed to break the real response (wrapped defensively since
    # `response` may be a Pydantic model or, in mocked tests, a plain dict).
    try:
        pagination = getattr(response, "pagination", None)
        if pagination is None and isinstance(response, dict):
            pagination = response.get("pagination") or {}
        total = getattr(pagination, "total", None)
        if total is None and isinstance(pagination, dict):
            total = pagination.get("total")
        logger.info(
            "[DIAG GET /conversations/provider] auth_user_id=%s resolved_provider_id=%s "
            "page=%s page_size=%s returned_count=%s",
            current_user.get("id"), current_user.get("id"),
            page, page_size, total,
        )
    except Exception:
        logger.debug("[DIAG GET /conversations/provider] diagnostic logging failed", exc_info=True)
    return response


@router.get(
    "/provider/archived",
    response_model=ProviderConversationPaginatedResponse,
    summary="List Provider Archived Conversations",
    description=(
        "List archived conversations for the authenticated provider. "
        "Each item includes `other_participant_user_id` for presence lookup. "
        "Same as `GET /conversations/provider?status=archived`."
    ),
)
def list_provider_archived_conversations(
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return list_provider_conversations_service(
        db, current_user, status_filter="archived", page=page, page_size=page_size
    )


@router.get(
    "/search",
    response_model=ConversationPaginatedResponse,
    summary="Search Conversations",
)
def search_conversations(
    q: str = Query(..., min_length=1, description="Search query."),
    provider_id: UUID | None = Query(None),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return search_conversations_service(
        db, current_user, search=q, provider_id=provider_id, page=page, page_size=page_size
    )


@router.get(
    "/{conversation_id}",
    response_model=ConversationResponse,
    summary="Get Conversation Details",
)
def get_conversation(
    conversation_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return get_conversation_service(db, current_user, conversation_id)


@router.patch(
    "/{conversation_id}/close",
    response_model=ConversationStatusUpdateResponse,
    summary="Close Conversation",
)
def close_conversation(
    conversation_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return close_conversation_service(db, current_user, conversation_id)


@router.patch(
    "/{conversation_id}/reopen",
    response_model=ConversationStatusUpdateResponse,
    summary="Reopen Conversation",
)
def reopen_conversation(
    conversation_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return reopen_conversation_service(db, current_user, conversation_id)


@router.patch(
    "/{conversation_id}/archive",
    response_model=ConversationArchiveResponse,
    summary="Archive or Unarchive Conversation",
    description=(
        "Toggle archive state with `{ \"archived\": true }` to archive or "
        "`{ \"archived\": false }` to restore to normal lists. "
        "Messages and history are unchanged."
    ),
)
def archive_conversation(
    conversation_id: UUID = Path(...),
    payload: ConversationArchiveRequest = Body(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return archive_conversation_service(
        db, current_user, conversation_id, archived=payload.archived
    )
