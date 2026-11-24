"""Pure role mapping shared by every service (copied from app/core/catalog_access.py)."""


def resolve_catalog_role(user: dict) -> str | None:
    """The catalog role (super_admin, admin, provider, customer) an authenticated user maps to, or None when the
    token/profile carries no application role this policy recognises."""
    role = str(user.get("role") or "").lower()
    tenant_role = str(user.get("tenant_role") or "").lower()
    if role == "super_admin":
        return role
    # Auth maps both tenant_admin and internal_user to provider. Distinguish
    # them here without altering the global role mapping used by other modules.
    if tenant_role == "internal_user" or role == "internal_user":
        role = "provider"
    elif tenant_role in ("tenant_owner", "tenant_admin"):
        role = "admin"
    return role if role in ("customer", "admin", "provider") else None
