"""Who may use the payment, currency, tax, refund and settlement APIs, from the authenticated claims only.

customer     any signed-in user paying for something; sees and acts on their own payments only
merchant     the business's Enterprise Admin / owner; sees the payments, refunds and settlements of their own business
super_admin  the platform's finance side; sees everything and manages currencies, rates, tax rules and commission
"""
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, Request

from app.core.catalog_access import CatalogAccess, get_catalog_access, get_optional_catalog_user
from app.core.dependencies import get_web_session_cookie_token


@dataclass(frozen=True)
class PayActor:
    id: str
    name: str | None
    email: str | None
    role: str                       # customer | merchant | super_admin
    tenant_id: UUID | None = None
    token: str | None = None

    @property
    def user_uuid(self) -> UUID:
        return UUID(self.id)

    @property
    def is_super_admin(self) -> bool:
        return self.role == "super_admin"


def _token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header.split(" ", 1)[1].strip() or None
    return get_web_session_cookie_token(request)


def _name(user: dict) -> str | None:
    name = str(user.get("name") or user.get("full_name") or "").strip()
    if not name:
        name = " ".join(str(user.get(k) or "").strip() for k in ("first_name", "last_name")).strip()
    return name or None


def _build(request: Request, access: CatalogAccess, user: dict | None) -> PayActor:
    if access.role == "public" or user is None:
        raise HTTPException(401, "Not authenticated")
    try:
        UUID(str(user.get("id")))
    except (ValueError, TypeError):
        raise HTTPException(403, "Authenticated user identity required")
    role = {"super_admin": "super_admin", "admin": "merchant"}.get(access.role, "customer")
    email = str(user.get("email") or "").strip().lower() or None
    return PayActor(id=str(user["id"]), name=_name(user), email=email, role=role,
                    tenant_id=access.tenant_id if role == "merchant" else None, token=_token(request))


def get_pay_actor(
    request: Request, access: CatalogAccess = Depends(get_catalog_access), user: dict | None = Depends(get_optional_catalog_user),
) -> PayActor:
    return _build(request, access, user)


def require_merchant_or_admin(actor: PayActor = Depends(get_pay_actor)) -> PayActor:
    if actor.role not in ("merchant", "super_admin"):
        raise HTTPException(403, "Only a business's Enterprise Admin or a Super Admin can do this")
    return actor


def require_super_admin(actor: PayActor = Depends(get_pay_actor)) -> PayActor:
    if actor.role != "super_admin":
        raise HTTPException(403, "Only a Super Admin can do this")
    return actor
