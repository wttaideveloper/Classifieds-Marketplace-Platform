"""Internal API of the commerce service (/internal/v1, shared key, never routed by the gateway)."""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from commerce_service.db.database import get_db
from commerce_service.startup import REPLICAS
from mskit.app import require_internal_key
from mskit.replication import subscriber_router

router = APIRouter(dependencies=[Depends(require_internal_key)], tags=["internal"])


@router.get("/users/{user_id}/confirmed-purchases/{product_id}")
def confirmed_purchase(user_id: UUID, product_id: UUID, db: Session = Depends(get_db)):
    """Verified-purchase badge on catalog reviews (monolith query, unchanged)."""
    from commerce_service.models.cart_model import Order, OrderItem

    found = (db.query(OrderItem).join(Order, Order.id == OrderItem.order_id)
             .filter(Order.user_id == user_id, Order.status == "confirmed", OrderItem.product_id == product_id).first())
    return {"purchased": found is not None}


routers = [router, subscriber_router(REPLICAS)]


# display names a user typed here (D8): rows in the monolith's order of chat_user_names._record_names
from uuid import UUID as _NUUID  # noqa: E402

from fastapi import APIRouter as _NAPIRouter, Depends as _NDepends  # noqa: E402
from pydantic import BaseModel as _NBaseModel  # noqa: E402

from commerce_service.db.database import get_db as _nget_db  # noqa: E402
from mskit.app import require_internal_key as _nkey  # noqa: E402

names_router = _NAPIRouter(dependencies=[_NDepends(_nkey)], tags=["internal"])


class _Names(_NBaseModel):
    user_ids: list[_NUUID]


@names_router.post("/names")
def names(body: _Names, db=_NDepends(_nget_db)):
    """Per source, [user_id, raw name] rows newest first (the caller cleans them and keeps the first real name)."""
    from commerce_service.models.cart_model import Order

    rows = (db.query(Order.user_id, Order.shipping_full_name).filter(Order.user_id.in_(body.user_ids), Order.shipping_full_name.isnot(None))
            .order_by(Order.created_at.desc()).all())
    return {"sources": [[[str(u), n] for u, n in rows]]}


routers = [*routers, names_router]
