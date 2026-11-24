"""Internal API of the identity service (/internal/v1, shared key, never routed by the gateway)."""

routers = []


# display names a user typed here (D8): rows in the monolith's order of chat_user_names._record_names
from uuid import UUID as _NUUID  # noqa: E402

from fastapi import APIRouter as _NAPIRouter, Depends as _NDepends  # noqa: E402
from pydantic import BaseModel as _NBaseModel  # noqa: E402

from identity_service.db.database import get_db as _nget_db  # noqa: E402
from mskit.app import require_internal_key as _nkey  # noqa: E402

names_router = _NAPIRouter(dependencies=[_NDepends(_nkey)], tags=["internal"])


class _Names(_NBaseModel):
    user_ids: list[_NUUID]


@names_router.post("/names")
def names(body: _Names, db=_NDepends(_nget_db)):
    """Per source, [user_id, raw name] rows newest first (the caller cleans them and keeps the first real name)."""
    from identity_service.models.address_model import UserAddress

    rows = db.query(UserAddress.user_id, UserAddress.full_name).filter(UserAddress.user_id.in_(body.user_ids), UserAddress.full_name.isnot(None)).all()
    return {"sources": [[[str(u), n] for u, n in rows]]}


routers = [*routers, names_router]
