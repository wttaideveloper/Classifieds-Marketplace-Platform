from math import ceil
import uuid

from sqlalchemy.orm import Session

from app.exceptions.custom_exception import CustomException
from app.models.admin_model import Business
from app.models.review_model import Review, ReviewModerationStatus
from app.repository.review_repo import (
    booking_already_reviewed,
    create_review_history_repo,
    create_review_repo,
    get_average_rating,
    get_review_by_id,
    get_reviews_by_business,
)


def _review_to_dict(review: Review) -> dict:
    name = None
    if review.customer:
        name = f"{review.customer.firstName} {review.customer.lastName}".strip()

    return {
        "review_id": str(review.id),
        "customer_name": name,
        "rating": review.rating,
        "review_comment": review.review_comment,
        "created_at": review.created_at,
    }


def create_review_service(db: Session, payload, current_user):
    try:
        if current_user.get("role") != "customer":
            raise CustomException(403, "Only customers can submit reviews")

        business = db.query(Business).filter(
            Business.id == payload.business_id,
            Business.status == "approved",
        ).first()
        if not business:
            raise CustomException(404, "Business not found")

        if booking_already_reviewed(db, payload.booking_id):
            raise CustomException(400, "A review already exists for this booking")

        review = Review(
            business_id=payload.business_id,
            customer_id=str(current_user.get("id")),
            booking_id=payload.booking_id,
            listing_id=payload.listing_id,
            rating=payload.rating,
            review_comment=payload.review_comment,
            moderation_status=ReviewModerationStatus.pending.value,
            is_verified=False,
        )
        review = create_review_repo(db, review)

        return {
            "review_id": str(review.id),
            "moderation_status": review.moderation_status,
            "created_at": review.created_at,
        }
    except CustomException:
        raise
    except Exception as exc:
        raise CustomException(500, str(exc))


def get_business_reviews_service(
    db: Session,
    business_id: str,
    page: int = 1,
    size: int = 10,
    rating: int = None,
):
    try:
        try:
            bid = uuid.UUID(business_id)
        except Exception:
            raise CustomException(400, "Invalid businessId")

        business = db.query(Business).filter(Business.id == bid).first()
        if not business:
            raise CustomException(404, "Business not found")

        total, reviews = get_reviews_by_business(db, bid, page, size, rating)
        avg = get_average_rating(db, bid)

        return {
            "page": page,
            "size": size,
            "total_elements": total,
            "total_pages": ceil(total / size) if size else 0,
            "average_rating": avg,
            "data": [_review_to_dict(review) for review in reviews],
        }
    except CustomException:
        raise
    except Exception as exc:
        raise CustomException(500, str(exc))


def moderate_review_service(db: Session, review_id: str, payload, current_user):
    try:
        if current_user.get("role") != "admin":
            raise CustomException(403, "Admin access required")

        try:
            rid = uuid.UUID(review_id)
        except Exception:
            raise CustomException(400, "Invalid review id")

        review = get_review_by_id(db, rid)
        if not review:
            raise CustomException(404, "Review not found")

        old_status = review.moderation_status
        review.moderation_status = payload.moderation_status.value
        db.commit()
        db.refresh(review)

        create_review_history_repo(
            db=db,
            review_id=review.id,
            old_status=old_status,
            new_status=review.moderation_status,
            moderated_by=int(current_user.get("id")),
            remarks=payload.remarks,
        )
        db.commit()

        return {
            "review_id": str(review.id),
            "moderation_status": review.moderation_status,
            "created_at": review.updated_at,
        }
    except CustomException:
        raise
    except Exception as exc:
        raise CustomException(500, str(exc))
