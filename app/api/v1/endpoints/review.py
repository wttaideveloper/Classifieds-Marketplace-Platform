from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.db.database import SessionLocal
from app.core.dependencies import get_current_user, get_current_admin
from app.schemas.review_schema import (
    ReviewCreateSchema,
    ReviewModerationSchema,
    PaginatedReviewsResponse,
    ReviewSubmissionResponse,
)
from app.repository.review_repo import (
    create_review_service,
    get_business_reviews_service,
    moderate_review_service,
)

router = APIRouter(tags=["Reviews & Ratings"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/reviews", status_code=201, response_model=ReviewSubmissionResponse)
def submit_review(
    payload: ReviewCreateSchema,
    db: Session = Depends(get_db)
):
    return create_review_service(db=db, payload=payload)


@router.get("/reviews/{businessId}", status_code=200, response_model=PaginatedReviewsResponse)
def get_reviews(
    businessId: str,
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=50),
    rating: Optional[int] = Query(None, ge=1, le=5),
    db: Session = Depends(get_db),
):
    return get_business_reviews_service(
        db=db,
        business_id=businessId,
        page=page,
        size=size,
        rating=rating,
    )


@router.put("/admin/reviews/{reviewId}", status_code=200)
def moderate_review(
    reviewId: str,
    payload: ReviewModerationSchema,
    db: Session = Depends(get_db)
):
    return moderate_review_service(db=db, review_id=reviewId, payload=payload)
