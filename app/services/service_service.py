from uuid import UUID
from app.core.catalog_access import validate_catalog_write

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.enterprise_model import Enterprise
from app.models.location_model import EnterpriseLocation
from app.repository.query_utils import build_pagination_meta
from app.repository.service_repo import (
    create_service,
    delete_service,
    get_service_by_id,
    get_services,
    update_service,
)
from app.schemas.service_schema import (
    ServiceDetailResponse,
    ServiceListItemResponse,
    ServicePaginatedResponse,
    ServiceResponse,
)
from app.services.response_mappers import (
    map_service_detail,
    map_service_list_item,
    map_service_write,
)


def _validate_references(db: Session, enterprise_id: UUID, location_id: UUID | None):
    enterprise = (
        db.query(Enterprise)
        .filter(
            Enterprise.id == enterprise_id,
            Enterprise.is_deleted.is_(False),
        )
        .first()
    )
    if not enterprise:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enterprise not found",
        )

    if location_id:
        location = (
            db.query(EnterpriseLocation)
            .filter(
                EnterpriseLocation.id == location_id,
                EnterpriseLocation.enterprise_id == enterprise_id,
                EnterpriseLocation.is_deleted.is_(False),
            )
            .first()
        )
        if not location:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Location not found for this enterprise",
            )


def create_service_service(db: Session, service_data, *, access=None):
    validate_catalog_write(db, service_data.enterprise_id, service_data.tenant_id, access)
    _validate_references(db, service_data.enterprise_id, service_data.location_id)
    return ServiceResponse.model_validate(
        map_service_write(create_service(db, service_data))
    )


def get_services_service(
    db: Session,
    *,
    search: str | None = None,
    tenant_id: UUID | None = None,
    enterprise_id: UUID | None = None,
    category: str | None = None,
    location_id: UUID | None = None,
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = 20,
    access=None,
) -> ServicePaginatedResponse:
    items, total = get_services(
        db,
        search=search,
        tenant_id=tenant_id,
        enterprise_id=enterprise_id,
        category=category,
        location_id=location_id,
        status=status_filter,
        page=page,
        page_size=page_size,
        access=access,
    )
    return ServicePaginatedResponse(
        items=[
            ServiceListItemResponse.model_validate(map_service_list_item(service))
            for service in items
        ],
        pagination=build_pagination_meta(total, page, page_size),
    )


def get_service_service(db: Session, service_id: UUID, *, access=None) -> ServiceDetailResponse:
    service = get_service_by_id(db, service_id, access=access)
    if not service:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service not found",
        )

    return ServiceDetailResponse.model_validate(map_service_detail(service, db))


def update_service_service(db: Session, service_id: UUID, update_data, *, access=None):
    service = get_service_by_id(db, service_id, include_deleted=True, access=access)
    if not service or service.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service not found",
        )

    validate_catalog_write(db, service.enterprise_id, update_data.tenant_id, access)
    location_id = update_data.location_id if update_data.location_id is not None else service.location_id
    _validate_references(db, service.enterprise_id, location_id)

    return ServiceResponse.model_validate(
        map_service_write(update_service(db, service, update_data))
    )


def delete_service_service(db: Session, service_id: UUID, *, access=None):
    service = get_service_by_id(db, service_id, access=access)
    if not service:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service not found",
        )

    validate_catalog_write(db, service.enterprise_id, None, access)
    return delete_service(db, service)


def _current_user_identity(current_user: dict) -> tuple[UUID, str | None]:
    user_id = current_user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authenticated user required")
    try:
        user_id = UUID(str(user_id))
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Authenticated user required")
    name = current_user.get("name") or current_user.get("email")
    return user_id, name


def create_service_review_service(db: Session, service_id: UUID, data, current_user: dict):
    from app.models.service_model import ServiceReview

    service = get_service_by_id(db, service_id)
    if not service:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")

    user_id, reviewer_name = _current_user_identity(current_user)
    # No booking/purchase record exists for Services today — always unverified.
    is_verified = False

    existing = db.query(ServiceReview).filter(ServiceReview.service_id == service_id, ServiceReview.user_id == user_id).first()
    if existing:
        existing.rating = str(data.rating)
        existing.comment = data.comment
        existing.reviewer_name = reviewer_name
        existing.is_verified_purchase = is_verified
        db.commit()
        db.refresh(existing)
        return existing

    review = ServiceReview(
        service_id=service_id, user_id=user_id, reviewer_name=reviewer_name,
        rating=str(data.rating), comment=data.comment, is_verified_purchase=is_verified,
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return review


def list_service_reviews_service(db: Session, service_id: UUID):
    from app.models.service_model import ServiceReview

    service = get_service_by_id(db, service_id)
    if not service:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")

    rows = (
        db.query(ServiceReview)
        .filter(ServiceReview.service_id == service_id, ServiceReview.moderation_status == "approved")
        .order_by(ServiceReview.created_at.desc())
        .all()
    )
    average = round(sum(int(r.rating) for r in rows) / len(rows), 2) if rows else None
    return {"reviews": rows, "average_rating": average, "total": len(rows)}


def delete_service_review_service(db: Session, service_id: UUID, review_id: UUID, current_user: dict):
    from app.models.service_model import ServiceReview

    user_id, _ = _current_user_identity(current_user)
    review = db.query(ServiceReview).filter(ServiceReview.id == review_id, ServiceReview.service_id == service_id).first()
    if not review:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")
    is_owner = review.user_id == user_id
    is_staff = str(current_user.get("role") or "").lower() in ("admin", "provider", "super_admin")
    if not is_owner and not is_staff:
        raise HTTPException(status_code=403, detail="You can only delete your own review")
    db.delete(review)
    db.commit()
    return {"message": "Review deleted"}


def moderate_service_review_service(db: Session, review_id: UUID, action: str):
    from app.models.service_model import ServiceReview

    allowed = {"approved", "rejected", "pending"}
    if action not in allowed:
        raise HTTPException(status_code=400, detail=f"Invalid moderation action. Allowed: {sorted(allowed)}")
    review = db.query(ServiceReview).filter(ServiceReview.id == review_id).first()
    if not review:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")
    review.moderation_status = action
    db.commit()
    db.refresh(review)
    return review
