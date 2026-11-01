"""Who may use the blog / news CMS, from the authenticated claims only.

Super Admin  -> /cms/blogs/admin/*  (writes platform posts, approves merchant posts, manages categories)
Merchant     -> /cms/blogs/my/*     (the Enterprise Admin / owner of a business, for that business's posts only)

An Enterprise Admin is deliberately not a Super Admin here: the approval step only means something if a merchant cannot
approve their own post.
"""
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Request

from app.exceptions.problem import problem

from app.core.catalog_access import CatalogAccess, get_catalog_access, get_optional_catalog_user
from app.core.dependencies import get_web_session_cookie_token


@dataclass(frozen=True)
class BlogActor:
    id: str
    name: str | None
    role: str                       # super_admin | merchant
    tenant_id: UUID | None = None
    token: str | None = None


def _token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header.split(" ", 1)[1].strip() or None
    return get_web_session_cookie_token(request)


def _display_name(user: dict) -> str | None:
    name = str(user.get("name") or user.get("full_name") or "").strip()
    if not name:
        name = " ".join(str(user.get(k) or "").strip() for k in ("first_name", "last_name")).strip()
    return name or (str(user.get("email") or "").strip() or None)


def _actor(request: Request, access: CatalogAccess, user: dict | None, role: str) -> BlogActor:
    if user is None or not user.get("id"):
        raise problem(401, "Not authenticated", "UNAUTHENTICATED")
    return BlogActor(id=str(user["id"]), name=_display_name(user), role=role, tenant_id=access.tenant_id, token=_token(request))


def require_blog_super_admin(
    request: Request, access: CatalogAccess = Depends(get_catalog_access), user: dict | None = Depends(get_optional_catalog_user),
) -> BlogActor:
    if access.role == "public":
        raise problem(401, "Not authenticated", "UNAUTHENTICATED")
    if access.role != "super_admin":
        raise problem(403, "Only a Super Admin can manage platform content and approve posts", "FORBIDDEN")
    return _actor(request, access, user, "super_admin")


def require_blog_merchant(
    request: Request, access: CatalogAccess = Depends(get_catalog_access), user: dict | None = Depends(get_optional_catalog_user),
) -> BlogActor:
    if access.role == "public":
        raise problem(401, "Not authenticated", "UNAUTHENTICATED")
    if access.role != "admin" or access.tenant_id is None:
        raise problem(403, "Only a business's Enterprise Admin can write posts for the business", "FORBIDDEN")
    return _actor(request, access, user, "merchant")


def optional_blog_actor(
    request: Request, access: CatalogAccess = Depends(get_catalog_access), user: dict | None = Depends(get_optional_catalog_user),
) -> BlogActor | None:
    """Whoever is signed in as a Super Admin or an Enterprise Admin, or nobody. For files that the public may also fetch."""
    if user is None or not user.get("id") or access.role not in ("super_admin", "admin"):
        return None
    if access.role == "admin" and access.tenant_id is None:
        return None
    return _actor(request, access, user, "super_admin" if access.role == "super_admin" else "merchant")


def require_blog_author(
    request: Request, access: CatalogAccess = Depends(get_catalog_access), user: dict | None = Depends(get_optional_catalog_user),
) -> BlogActor:
    """A Super Admin or an Enterprise Admin: whoever may write posts."""
    if access.role == "public":
        raise problem(401, "Not authenticated", "UNAUTHENTICATED")
    if access.role == "super_admin":
        return _actor(request, access, user, "super_admin")
    if access.role == "admin" and access.tenant_id is not None:
        return _actor(request, access, user, "merchant")
    raise problem(403, "Only a Super Admin or a business's Enterprise Admin can upload files for posts", "FORBIDDEN")
