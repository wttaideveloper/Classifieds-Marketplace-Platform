"""Who is calling, for order administration (copied from app/services/service_booking_service.py: actor_from)."""
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session


def _err(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"message": message, "code": code, **extra})


@dataclass
class Actor:
    id: UUID
    role: str                      # customer | admin | provider | super_admin
    tenant_id: UUID | None = None
    name: str | None = None
    email: str | None = None
    token: str | None = None            # the caller's bearer token (the identity lookup of the enterprise's admins needs one)

    @property
    def is_staff(self) -> bool:
        return self.role in ("admin", "provider", "super_admin")


def actor_from(db: Session, user: dict, access_token: str | None = None) -> Actor:
    from mskit.auth.auth_context import resolve_auth_tenant_id_with_db
    from mskit.auth.roles import resolve_catalog_role

    try:
        uid = UUID(str(user.get("id")))
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid user identity")
    role = resolve_catalog_role(user) or "customer"
    tenant = None
    if role in ("admin", "provider"):
        try:
            tenant = UUID(str(resolve_auth_tenant_id_with_db(db, user, access_token=access_token)))
        except (ValueError, TypeError):
            raise _err(403, "TENANT_REQUIRED", "Authenticated tenant identity required")
    name = " ".join(str(user.get("name") or "").split())
    return Actor(id=uid, role=role, tenant_id=tenant, name=None if (not name or "@" in name) else name, email=(user.get("email") or None), token=access_token)

