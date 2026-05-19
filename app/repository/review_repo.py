from math import ceil
import uuid
from enum import Enum
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.review_model import Review, ReviewModerationStatus
from app.models.review_moderation_history_model import ReviewModerationHistory
from app.models.admin_model import Business
from app.models.customer_model import Customer
from app.exceptions.custom_exception import CustomException


class ReviewQueryStatus(Enum):
    approved = ReviewModerationStatus.approved.value


# ─── REPO ────────────────────────────────────────────────

def create_review_repo(db: Session, review: Review) -> Review:
    db.add(review)
    db.commit()
    db.refresh(review)
    return review


def create_review_history_repo(
    db: Session,
    review_id,
    old_status: str,
    new_status: str,
    moderated_by: int,
    remarks: str = None,
) -> ReviewModerationHistory:
    history = ReviewModerationHistory(
        review_id=review_id,
        old_status=old_status,
        new_status=new_status,
        moderated_by=moderated_by,
        remarks=remarks,
    )
    db.add(history)
    db.commit()
    db.refresh(history)
    return history


def get_review_by_id(db: Session, review_id):
    return db.query(Review).filter(Review.id == review_id).first()


def get_reviews_by_business(
    db: Session,
    business_id,
    page: int = 1,
    size: int = 10,
    rating: int = None,
):
    query = db.query(Review).filter(
        Review.business_id == business_id,
        Review.moderation_status == ReviewQueryStatus.approved.value,
    )

    if rating is not None:
        query = query.filter(Review.rating == rating)

    query = query.order_by(Review.created_at.desc())
    total = query.count()
    skip = (page - 1) * size
    reviews = query.offset(skip).limit(size).all()
    return total, reviews


def get_average_rating(db: Session, business_id) -> float:
    result = db.query(func.avg(Review.rating)).filter(
        Review.business_id == business_id,
        Review.moderation_status == ReviewQueryStatus.approved.value,
    ).scalar()
    return round(float(result), 1) if result else 0.0


def update_review_repo(db: Session, review: Review) -> Review:
    db.commit()
    db.refresh(review)
    return review


def booking_already_reviewed(db: Session, booking_id) -> bool:
    return db.query(Review).filter(
        Review.booking_id == booking_id
    ).first() is not None


def _review_to_dict(review: Review, customer: Customer = None) -> dict:
    name = None
    if customer:
        name = f"{customer.firstName} {customer.lastName}".strip()
    elif review.customer:
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
    except Exception as e:
        raise CustomException(500, str(e))


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
            "data": [_review_to_dict(r) for r in reviews],
        }
    except CustomException:
        raise
    except Exception as e:
        raise CustomException(500, str(e))


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
        review = update_review_repo(db, review)

        create_review_history_repo(
            db=db,
            review_id=review.id,
            old_status=old_status,
            new_status=review.moderation_status,
            moderated_by=int(current_user.get("id")),
            remarks=payload.remarks,
        )

        return {
            "review_id": str(review.id),
            "moderation_status": review.moderation_status,
            "created_at": review.updated_at,
        }
    except CustomException:
        raise
    except Exception as e:
        raise CustomException(500, str(e))
