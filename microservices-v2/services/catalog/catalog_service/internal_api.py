"""Internal API of the catalog service (/internal/v1, shared key, never routed by the gateway).

Every query is the one the other domains used to run on the shared enterprises table.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from catalog_service.db.database import get_db
from catalog_service.models.enterprise_model import Enterprise
from mskit.app import require_internal_key

router = APIRouter(dependencies=[Depends(require_internal_key)], tags=["internal"])


def _live(db: Session):
    return db.query(Enterprise).filter(Enterprise.is_deleted.is_(False))


def _card(e: Enterprise) -> dict:
    return {
        "id": str(e.id),
        "tenant_id": str(e.tenant_id) if e.tenant_id else None,
        "business_short_name": e.business_short_name,
        "business_legal_name": e.business_legal_name,
        "logo_url": e.logo_url,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


@router.get("/enterprises/{enterprise_id}/tenant")
def enterprise_tenant(enterprise_id: UUID, db: Session = Depends(get_db)):
    """auth_context: SELECT tenant_id FROM enterprises WHERE id = :id AND NOT is_deleted."""
    e = _live(db).filter(Enterprise.id == enterprise_id).first()
    if e is None or not e.tenant_id:
        raise HTTPException(404, "Not found")
    return {"tenant_id": str(e.tenant_id)}


@router.get("/tenants/{tenant_id}/enterprise")
def tenant_enterprise(tenant_id: UUID, enterprise_id: UUID | None = Query(None), db: Session = Depends(get_db)):
    """The given enterprise of the tenant, else the tenant's oldest live enterprise (blogs: business of a post)."""
    q = _live(db).filter(Enterprise.tenant_id == tenant_id)
    e = q.filter(Enterprise.id == enterprise_id).first() if enterprise_id else q.order_by(Enterprise.created_at).first()
    if e is None:
        raise HTTPException(404, "No enterprise")
    return _card(e)


class TenantIds(BaseModel):
    tenant_ids: list[UUID]


@router.post("/enterprises/business-names")
def business_names(payload: TenantIds, db: Session = Depends(get_db)):
    """{tenant_id: short or legal name} of each tenant's oldest live enterprise."""
    if not payload.tenant_ids:
        return {}
    rows = _live(db).filter(Enterprise.tenant_id.in_(payload.tenant_ids)).order_by(Enterprise.created_at).all()
    names: dict[str, str] = {}
    for row in rows:
        names.setdefault(str(row.tenant_id), row.business_short_name or row.business_legal_name)
    return names


# replica snapshots for subscribers (events, learning, booking, ...): GET /internal/v1/replica/{topic}
from mskit.replication import publisher_router  # noqa: E402

from catalog_service.db.database import SessionLocal  # noqa: E402
from catalog_service.startup import PUBLISHER  # noqa: E402

from catalog_service.stock_api import router as stock_router  # noqa: E402

routers = [router, publisher_router(PUBLISHER, SessionLocal), stock_router]


# review moderation queue (D6): this service's slice + moderate/delete, called by moderation
from catalog_service.db.database import get_db as _get_db  # noqa: E402
from catalog_service.review_queue_adapter import CatalogReviews  # noqa: E402
from mskit.review_queue import owner_router  # noqa: E402

routers = [*routers, owner_router(CatalogReviews(), _get_db)]


# display names a user typed here (D8): rows in the monolith's order of chat_user_names._record_names
from uuid import UUID as _NUUID  # noqa: E402

from fastapi import APIRouter as _NAPIRouter, Depends as _NDepends  # noqa: E402
from pydantic import BaseModel as _NBaseModel  # noqa: E402

from catalog_service.db.database import get_db as _nget_db  # noqa: E402
from mskit.app import require_internal_key as _nkey  # noqa: E402

names_router = _NAPIRouter(dependencies=[_NDepends(_nkey)], tags=["internal"])


class _Names(_NBaseModel):
    user_ids: list[_NUUID]


@names_router.post("/names")
def names(body: _Names, db=_NDepends(_nget_db)):
    """Per source, [user_id, raw name] rows newest first (the caller cleans them and keeps the first real name)."""
    from catalog_service.models.product_model import ProductReview
    from catalog_service.models.service_model import ServiceReview

    out = []
    for model in (ProductReview, ServiceReview):
        rows = (db.query(model.user_id, model.reviewer_name).filter(model.user_id.in_(body.user_ids), model.reviewer_name.isnot(None))
                .order_by(model.created_at.desc()).all())
        out.append([[str(u), n] for u, n in rows])
    return {"sources": out}


routers = [*routers, names_router]
