from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Fixed test user IDs — use these in dev tokens and seed data.
TEST_ADMIN_USER_ID = "550e8400-e29b-41d4-a716-446655440000"
TEST_PROVIDER_USER_ID = "550e8400-e29b-41d4-a716-446655440020"
TEST_CUSTOMER_USER_ID = "550e8400-e29b-41d4-a716-446655440030"

DEFAULT_DEV_USER_ID = TEST_ADMIN_USER_ID


class DevTokenRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_id": TEST_PROVIDER_USER_ID,
                "role": "provider",
                "email": "provider@test.com",
            }
        }
    )

    user_id: UUID | None = Field(
        default=None,
        description=(
            "Optional user ID for the token. "
            f"Defaults to `{DEFAULT_DEV_USER_ID}`. "
            f"For `/admin/messages`, use provider ID `{TEST_PROVIDER_USER_ID}`."
        ),
    )
    role: Literal["customer", "provider", "admin"] = Field(
        default="provider",
        description="Role for the token. Use `provider` for `/admin/messages`.",
    )
    email: str | None = Field(
        default="provider@test.com",
        description="Email stored in the JWT payload. Any string is accepted.",
    )


class TokenResponse(BaseModel):
    access_token: str = Field(..., description="JWT access token.")
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Token lifetime in seconds.")
    user: dict = Field(..., description="User claims embedded in the token.")


class TestUsersResponse(BaseModel):
    admin_user_id: str
    provider_user_id: str
    customer_user_id: str
    recommended_for_admin_messages: str = "provider"
    notes: list[str] = Field(default_factory=list)


class AuthRoleMapping(BaseModel):
    tenant_role: str
    marketplace_role: str


class AuthIntegrationResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "auth_provider": "Invigorate Healthcare Auth (Keycloak RS256)",
                "login_base_url": "https://p6wvqog202.execute-api.us-east-1.amazonaws.com",
                "login_endpoint": "POST /api/v1/auth/login",
                "token_response_path": "tokens.access_token",
                "authorization_header": "Authorization: Bearer <tokens.access_token>",
                "algorithm": "RS256",
                "issuer": "https://auth-dev.onruyl.com/realms/invigorate-healthcare",
                "audience": "invigorate-api",
                "jwks_url": "https://auth-dev.onruyl.com/realms/invigorate-healthcare/protocol/openid-connect/certs",
                "user_id_claim": "sub",
                "application_user_uuid_endpoint": "GET /api/v1/auth/me",
                "role_claims": [
                    "tenant_role",
                    "user_role",
                    "tenant_rbac_roles",
                    "tenant_permissions",
                ],
                "role_mapping": [
                    {"tenant_role": "external_user", "marketplace_role": "customer"},
                    {"tenant_role": "tenant_admin", "marketplace_role": "provider"},
                    {"tenant_role": "internal_user", "marketplace_role": "provider"},
                    {"tenant_role": "tenant_owner", "marketplace_role": "admin"},
                ],
                "dev_token_endpoint": "GET /api/v1/auth/dev-token",
                "dev_token_enabled": False,
                "notes": [
                    "Use tokens.access_token from Invigorate login for this marketplace API.",
                    "Application User UUID from GET /api/v1/auth/me differs from JWT sub.",
                ],
            }
        }
    )

    auth_provider: str
    login_base_url: str
    login_endpoint: str
    token_response_path: str
    authorization_header: str
    algorithm: str
    issuer: str
    audience: str
    jwks_url: str
    user_id_claim: str
    application_user_uuid_endpoint: str
    role_claims: list[str]
    role_mapping: list[AuthRoleMapping]
    dev_token_endpoint: str
    dev_token_enabled: bool
    notes: list[str] = Field(default_factory=list)
