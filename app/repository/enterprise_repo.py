from uuid import UUID

from sqlalchemy.orm import Session

from app.models.enterprise_model import Enterprise
from app.repository.query_utils import (
    apply_ilike_search,
    apply_soft_delete_filter,
    paginate_query,
)


def _sync_legacy_fields(data: dict) -> dict:
    payload = dict(data)
    if payload.get("website") and not payload.get("website_url"):
        payload["website_url"] = payload["website"]
    if payload.get("banner_url") and not payload.get("business_images"):
        payload["business_images"] = payload["banner_url"]
    return payload


def create_enterprise(db: Session, enterprise_data):
    payload = _sync_legacy_fields(enterprise_data.model_dump())
    enterprise = Enterprise(**payload)
    db.add(enterprise)
    db.commit()
    db.refresh(enterprise)
    return enterprise


def get_enterprises(
    db: Session,
    *,
    search: str | None = None,
    status: str | None = None,
    tenant_id: UUID | None = None,
    page: int = 1,
    page_size: int = 20,
    include_deleted: bool = False,
):
    query = db.query(Enterprise)
    query = apply_soft_delete_filter(query, Enterprise, include_deleted)

    if tenant_id:
        query = query.filter(Enterprise.tenant_id == tenant_id)
    if status:
        query = query.filter(Enterprise.status == status)
    if search:
        query = apply_ilike_search(
            query,
            [
                Enterprise.business_short_name,
                Enterprise.business_legal_name,
                Enterprise.business_description,
                Enterprise.business_email,
                Enterprise.business_category,
            ],
            search,
        )

    query = query.order_by(Enterprise.created_at.desc())
    return paginate_query(query, page, page_size)


def get_enterprise_by_id(db: Session, enterprise_id: UUID, include_deleted: bool = False):
    query = db.query(Enterprise).filter(Enterprise.id == enterprise_id)
    if not include_deleted:
        query = apply_soft_delete_filter(query, Enterprise, include_deleted)
    return query.first()


def get_enterprise_by_email(db: Session, business_email: str):
    return (
        db.query(Enterprise)
        .filter(
            Enterprise.business_email == business_email,
            Enterprise.is_deleted.is_(False),
        )
        .first()
    )


def update_enterprise(db: Session, enterprise, update_data):
    payload = _sync_legacy_fields(update_data.model_dump(exclude_unset=True))
    for key, value in payload.items():
        setattr(enterprise, key, value)
    db.commit()
    db.refresh(enterprise)
    return enterprise


def delete_enterprise(db: Session, enterprise):
    enterprise.is_deleted = True
    enterprise.status = "inactive"
    db.commit()
    db.refresh(enterprise)
    return enterprise
