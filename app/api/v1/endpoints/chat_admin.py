from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_admin
from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.chat_schema import (
    ChatDashboardResponse,
    ChatExportResponse,
    ConversationPaginatedResponse,
)
from app.services.chat_service import (
    admin_dashboard_service,
    admin_list_conversations_service,
    export_conversation_service,
)

router = APIRouter(tags=["Chat Administration"])


@router.get(
    "/dashboard",
    response_model=ChatDashboardResponse,
    summary="Chat Dashboard",
    description="Admin overview of conversation and message statistics.",
)
def chat_dashboard(
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    return admin_dashboard_service(db)


@router.get(
    "/conversations",
    response_model=ConversationPaginatedResponse,
    summary="List All Conversations (Admin)",
)
def admin_list_conversations(
    status_filter: str | None = Query(None, alias="status"),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    return admin_list_conversations_service(
        db, status_filter=status_filter, page=page, page_size=page_size
    )


@router.get(
    "/conversations/active",
    response_model=ConversationPaginatedResponse,
    summary="Active Conversations",
)
def active_conversations(
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    return admin_list_conversations_service(
        db, status_filter="open", page=page, page_size=page_size
    )


@router.get(
    "/conversations/closed",
    response_model=ConversationPaginatedResponse,
    summary="Closed Conversations",
)
def closed_conversations(
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    return admin_list_conversations_service(
        db, status_filter="closed", page=page, page_size=page_size
    )


@router.get(
    "/conversations/archived",
    response_model=ConversationPaginatedResponse,
    summary="Archived Conversations",
)
def archived_conversations(
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    return admin_list_conversations_service(
        db, status_filter="archived", page=page, page_size=page_size
    )


@router.get(
    "/conversations/{conversation_id}/export",
    response_model=ChatExportResponse,
    summary="Export Chat History",
)
def export_chat_history(
    conversation_id: UUID = Path(...),
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    return export_conversation_service(db, conversation_id)
