from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_admin
from app.db.database import SessionLocal
from app.schemas.moderation_schema import (
    ModerationActionResponse,
    PaginatedReportsResponse,
    ReportedContentResponse,
    ReportStatusEnum,
    UpdateBlogStatusSchema,
    UpdateEntityStatusSchema,
    UpdateReportStatusSchema,
    UpdateReviewStatusSchema,
)
from app.services.moderation_service import (
    list_moderation_reports_service,
    update_blog_status_service,
    update_business_status_service,
    update_listing_status_service,
    update_merchant_status_service,
    update_report_status_service,
    update_review_status_service,
)

router = APIRouter(tags=["Admin Moderation"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.put(
    "/business/{entity_id}/status",
    status_code=status.HTTP_200_OK,
    response_model=ModerationActionResponse,
    summary="Update business moderation status",
)
def update_business_status(
    entity_id: str,
    payload: UpdateEntityStatusSchema,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    return update_business_status_service(db, entity_id, payload, current_user)


@router.put(
    "/listings/{entity_id}/status",
    status_code=status.HTTP_200_OK,
    response_model=ModerationActionResponse,
    summary="Update listing moderation status",
)
def update_listing_status(
    entity_id: str,
    payload: UpdateEntityStatusSchema,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    return update_listing_status_service(db, entity_id, payload, current_user)


@router.put(
    "/merchant/{entity_id}/status",
    status_code=status.HTTP_200_OK,
    response_model=ModerationActionResponse,
    summary="Update merchant status (activation/deactivation)",
)
def update_merchant_status(
    entity_id: str,
    payload: UpdateEntityStatusSchema,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    return update_merchant_status_service(db, entity_id, payload, current_user)


@router.put(
    "/reviews/{entity_id}/status",
    status_code=status.HTTP_200_OK,
    response_model=ModerationActionResponse,
    summary="Moderate review (approve/reject)",
)
def update_review_status(
    entity_id: str,
    payload: UpdateReviewStatusSchema,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    return update_review_status_service(db, entity_id, payload, current_user)


@router.put(
    "/blogs/{entity_id}/status",
    status_code=status.HTTP_200_OK,
    response_model=ModerationActionResponse,
    summary="Moderate blog content (approve/reject)",
)
def update_blog_status(
    entity_id: str,
    payload: UpdateBlogStatusSchema,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    return update_blog_status_service(db, entity_id, payload, current_user)


@router.get(
    "/moderation/reports",
    status_code=status.HTTP_200_OK,
    response_model=PaginatedReportsResponse,
    summary="List reported content for admin review",
)
def list_moderation_reports(
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=50),
    report_status: Optional[ReportStatusEnum] = Query(
        None, description="Pending | Reviewed | Resolved"
    ),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    return list_moderation_reports_service(
        db,
        page,
        size,
        report_status.value if report_status else None,
    )


@router.put(
    "/moderation/reports/{report_id}/status",
    status_code=status.HTTP_200_OK,
    response_model=ReportedContentResponse,
    summary="Update reported content review status",
)
def update_report_status(
    report_id: str,
    payload: UpdateReportStatusSchema,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin),
):
    return update_report_status_service(db, report_id, payload, current_user)
