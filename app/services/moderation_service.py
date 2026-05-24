from datetime import datetime, timezone
from uuid import UUID

from fastapi import status
from sqlalchemy.orm import Session

from app.exceptions.custom_exception import CustomException
from app.models.admin_model import Business
from app.models.merchant_model import Merchant
from app.models.moderation_model import ModerationEntityType, ModerationLog
from app.models.review_model import Review
from app.repository.admin_repo import get_business_by_id, get_listing_by_id_repo
from app.repository.merchant_repo import get_merchant_by_id
from app.repository.moderation_repo import (
    create_moderation_log,
    get_report_by_id,
    list_reported_content,
)
from app.repository.review_repo import (
    create_review_history_repo,
    get_review_by_id,
)
from app.schemas.moderation_schema import (
    UpdateBlogStatusSchema,
    UpdateEntityStatusSchema,
    UpdateReportStatusSchema,
    UpdateReviewStatusSchema,
)
from app.schemas.review_schema import ReviewModerationStatus


def _parse_uuid(value: str, field_name: str) -> UUID:
    try:
        return UUID(str(value))
    except Exception:
        raise CustomException(400, f"Invalid {field_name}")


def _admin_uuid(admin_id: str | int) -> UUID:
    return UUID(int=int(admin_id))


def _normalize_status(value: str) -> str:
    return value.strip().lower()


def _response(entity_id: UUID, old_status: str, new_status: str, admin_id: str, updated_at) -> dict:
    return {
        "success": True,
        "message": "Status updated successfully",
        "entity_id": str(entity_id),
        "old_status": old_status,
        "new_status": new_status,
        "updated_by": str(admin_id),
        "updated_at": updated_at,
    }


def _write_log(
    db: Session,
    *,
    entity_type: str,
    entity_id: UUID,
    old_status: str,
    new_status: str,
    admin_id: str,
    remarks: str | None,
) -> None:
    create_moderation_log(
        db,
        ModerationLog(
            entity_type=entity_type,
            entity_id=entity_id,
            old_status=old_status or "unknown",
            new_status=new_status,
            moderated_by=_admin_uuid(admin_id),
            remarks=remarks,
        ),
    )


def update_business_status_service(
    db: Session, business_id: str, payload: UpdateEntityStatusSchema, current_user: dict
):
    try:
        bid = _parse_uuid(business_id, "business_id")
        business = get_business_by_id(db, bid)
        if not business:
            raise CustomException(404, "Business not found")

        new_status = _normalize_status(payload.status.value)
        old_status = business.status or "unknown"
        now = datetime.now(timezone.utc)

        if new_status == "approved":
            business.status = "approved"
            business.approved_at = now
        elif new_status == "rejected":
            business.status = "rejected"
            business.rejection_reason = payload.remarks
            business.rejected_at = now
        elif new_status == "suspended":
            business.status = "suspended"
            business.suspension_reason = payload.remarks
            business.suspended_at = now
        elif new_status == "pending":
            business.status = "pending"
        elif new_status == "active":
            business.status = "approved"
            business.suspension_reason = None
            business.suspended_at = None
        elif new_status == "inactive":
            business.status = "suspended"
            business.suspension_reason = payload.remarks or "Deactivated by admin"
            business.suspended_at = now
        else:
            raise CustomException(400, "Invalid status for business")

        _write_log(
            db,
            entity_type=ModerationEntityType.Business.value,
            entity_id=business.id,
            old_status=old_status,
            new_status=business.status,
            admin_id=current_user["id"],
            remarks=payload.remarks,
        )
        db.commit()
        db.refresh(business)
        return _response(business.id, old_status, business.status, current_user["id"], now)
    except CustomException:
        raise
    except Exception as e:
        db.rollback()
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def update_listing_status_service(
    db: Session, listing_id: str, payload: UpdateEntityStatusSchema, current_user: dict
):
    try:
        lid = _parse_uuid(listing_id, "listing_id")
        listing = get_listing_by_id_repo(db, lid)
        if not listing:
            raise CustomException(404, "Listing not found")

        new_status = _normalize_status(payload.status.value)
        old_status = listing.status or "unknown"
        now = datetime.utcnow()

        if new_status == "approved":
            listing.status = "published"
            listing.approvedAt = now
        elif new_status == "rejected":
            listing.status = "rejected"
            listing.rejectionReason = payload.remarks
            listing.rejectedAt = now
        elif new_status == "suspended":
            listing.status = "suspended"
            listing.suspensionReason = payload.remarks
            listing.suspendedAt = now
        elif new_status == "pending":
            listing.status = "pending"
        elif new_status == "active":
            listing.status = "published"
            listing.suspendedAt = None
            listing.suspensionReason = None
        elif new_status == "inactive":
            listing.status = "suspended"
            listing.suspensionReason = payload.remarks or "Deactivated by admin"
            listing.suspendedAt = now
        else:
            raise CustomException(400, "Invalid status for listing")

        _write_log(
            db,
            entity_type=ModerationEntityType.Listing.value,
            entity_id=listing.id,
            old_status=old_status,
            new_status=listing.status,
            admin_id=current_user["id"],
            remarks=payload.remarks,
        )
        db.commit()
        db.refresh(listing)
        return _response(listing.id, old_status, listing.status, current_user["id"], listing.updated_at)
    except CustomException:
        raise
    except Exception as e:
        db.rollback()
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def update_merchant_status_service(
    db: Session, merchant_id: str, payload: UpdateEntityStatusSchema, current_user: dict
):
    try:
        mid = _parse_uuid(merchant_id, "merchant_id")
        merchant = get_merchant_by_id(db, str(mid))
        if not merchant:
            raise CustomException(404, "Merchant not found")

        new_status = _normalize_status(payload.status.value)
        old_status = merchant.status or "unknown"

        if new_status in {"approved", "active"}:
            merchant.status = "active"
        elif new_status in {"inactive", "suspended"}:
            merchant.status = "suspended" if new_status == "suspended" else "inactive"
        elif new_status == "pending":
            merchant.status = "pending"
        elif new_status == "rejected":
            merchant.status = "rejected"
        else:
            raise CustomException(400, "Invalid status for merchant")

        _write_log(
            db,
            entity_type=ModerationEntityType.Merchant.value,
            entity_id=merchant.id,
            old_status=old_status,
            new_status=merchant.status,
            admin_id=current_user["id"],
            remarks=payload.remarks,
        )
        db.commit()
        db.refresh(merchant)
        return _response(merchant.id, old_status, merchant.status, current_user["id"], merchant.updatedAt)
    except CustomException:
        raise
    except Exception as e:
        db.rollback()
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def update_review_status_service(
    db: Session, review_id: str, payload: UpdateReviewStatusSchema, current_user: dict
):
    try:
        rid = _parse_uuid(review_id, "review_id")
        review = get_review_by_id(db, rid)
        if not review:
            raise CustomException(404, "Review not found")

        old_status = review.moderation_status
        new_status = payload.moderation_status.value
        review.moderation_status = new_status

        create_review_history_repo(
            db=db,
            review_id=review.id,
            old_status=old_status,
            new_status=new_status,
            moderated_by=int(current_user.get("id")),
            remarks=payload.remarks,
        )
        _write_log(
            db,
            entity_type=ModerationEntityType.Review.value,
            entity_id=review.id,
            old_status=old_status,
            new_status=new_status,
            admin_id=current_user["id"],
            remarks=payload.remarks,
        )
        db.commit()
        db.refresh(review)
        return _response(review.id, old_status, new_status, current_user["id"], review.updated_at)
    except CustomException:
        raise
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def update_blog_status_service(
    db: Session, blog_id: str, payload: UpdateBlogStatusSchema, current_user: dict
):
    try:
        from app.repository.blog_repo import get_blog_by_id
        from app.models.blog_model import BlogApprovalLog

        bid = _parse_uuid(blog_id, "blog_id")
        blog = get_blog_by_id(db, bid)
        if not blog:
            raise CustomException(404, "Blog not found")

        old_status = blog.approval_status or "PENDING"
        mod = payload.moderation_status.value

        if mod == ReviewModerationStatus.approved.value:
            blog.approval_status = "APPROVED"
            blog.status = "PUBLISHED"
            blog.is_published = True
            action = "APPROVED"
        elif mod == ReviewModerationStatus.rejected.value:
            blog.approval_status = "REJECTED"
            blog.status = "DRAFT"
            blog.is_published = False
            action = "REJECTED"
        else:
            blog.approval_status = mod.upper()
            action = mod.upper()

        db.add(
            BlogApprovalLog(
                blog_id=bid,
                admin_id=int(current_user.get("id")),
                action=action,
                remarks=payload.remarks,
            )
        )

        _write_log(
            db,
            entity_type=ModerationEntityType.Blog.value,
            entity_id=blog.id,
            old_status=old_status,
            new_status=blog.approval_status,
            admin_id=current_user["id"],
            remarks=payload.remarks,
        )
        db.commit()
        db.refresh(blog)
        return _response(blog.id, old_status, blog.approval_status, current_user["id"], blog.updated_at)
    except CustomException:
        raise
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def list_moderation_reports_service(
    db: Session,
    page: int,
    size: int,
    report_status: str | None = None,
):
    try:
        total, rows, total_pages = list_reported_content(db, report_status, page, size)
        return {
            "success": True,
            "page": page,
            "size": size,
            "total_elements": total,
            "total_pages": total_pages,
            "data": [
                {
                    "id": str(r.id),
                    "reported_by": str(r.reported_by),
                    "entity_type": r.entity_type,
                    "entity_id": str(r.entity_id),
                    "reason": r.reason,
                    "report_status": r.report_status,
                    "reviewed_by": str(r.reviewed_by) if r.reviewed_by else None,
                    "remarks": r.remarks,
                    "created_at": r.created_at,
                    "updated_at": r.updated_at,
                }
                for r in rows
            ],
        }
    except Exception as e:
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))


def update_report_status_service(
    db: Session,
    report_id: str,
    payload: UpdateReportStatusSchema,
    current_user: dict,
):
    try:
        rid = _parse_uuid(report_id, "report_id")
        report = get_report_by_id(db, rid)
        if not report:
            raise CustomException(404, "Report not found")

        report.report_status = payload.report_status.value
        report.reviewed_by = _admin_uuid(current_user["id"])
        report.remarks = payload.remarks

        db.commit()
        db.refresh(report)

        return {
            "id": str(report.id),
            "reported_by": str(report.reported_by),
            "entity_type": report.entity_type,
            "entity_id": str(report.entity_id),
            "reason": report.reason,
            "report_status": report.report_status,
            "reviewed_by": str(report.reviewed_by) if report.reviewed_by else None,
            "remarks": report.remarks,
            "created_at": report.created_at,
            "updated_at": report.updated_at,
        }
    except CustomException:
        raise
    except Exception as e:
        db.rollback()
        raise CustomException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e))
