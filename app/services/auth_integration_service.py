from app.core.config import settings
from app.schemas.auth_schema import AuthIntegrationResponse, AuthRoleMapping

INVIGORATE_LOGIN_BASE_URL = "https://p6wvqog202.execute-api.us-east-1.amazonaws.com"
INVIGORATE_ISSUER = "https://auth-dev.onruyl.com/realms/invigorate-healthcare"
INVIGORATE_JWKS_URL = (
    "https://auth-dev.onruyl.com/realms/invigorate-healthcare/protocol/openid-connect/certs"
)
INVIGORATE_AUDIENCE = "invigorate-api"


def get_auth_integration_info() -> AuthIntegrationResponse:
    issuer = settings.KEYCLOAK_ISSUER.strip() or INVIGORATE_ISSUER
    audience = settings.KEYCLOAK_AUDIENCE.strip() or INVIGORATE_AUDIENCE
    jwks_url = settings.keycloak_jwks_url if settings.keycloak_configured else INVIGORATE_JWKS_URL

    dev_enabled = (not settings.is_production) or settings.ENABLE_DEV_TOKEN

    return AuthIntegrationResponse(
        auth_provider="Invigorate Healthcare Auth (Keycloak RS256)",
        login_base_url=INVIGORATE_LOGIN_BASE_URL,
        login_endpoint="POST /api/v1/auth/login",
        token_response_path="tokens.access_token",
        authorization_header="Authorization: Bearer <tokens.access_token>",
        algorithm="RS256",
        issuer=issuer,
        audience=audience,
        jwks_url=jwks_url,
        user_id_claim="sub",
        application_user_uuid_endpoint="GET /api/v1/auth/me",
        role_claims=[
            "tenant_role",
            "user_role",
            "tenant_rbac_roles",
            "tenant_permissions",
        ],
        role_mapping=[
            AuthRoleMapping(tenant_role="external_user", marketplace_role="customer"),
            AuthRoleMapping(tenant_role="tenant_admin", marketplace_role="provider"),
            AuthRoleMapping(tenant_role="internal_user", marketplace_role="provider"),
            AuthRoleMapping(tenant_role="tenant_owner", marketplace_role="admin"),
        ],
        dev_token_endpoint="GET /api/v1/auth/dev-token",
        dev_token_enabled=dev_enabled,
        notes=[
            "Primary login: POST https://p6wvqog202.execute-api.us-east-1.amazonaws.com/api/v1/auth/login",
            "Use tokens.access_token from the login response in the Authorize button above.",
            "This marketplace API validates RS256 tokens using issuer, JWKS, and KEYCLOAK_AUDIENCE.",
            "Audience check accepts aud=invigorate-api OR azp=invigorate-api (Keycloak access tokens often use azp).",
            "JWT sub is used as user_id here. Application User UUID is available via GET /api/v1/auth/me on the auth API.",
            "Dev token endpoints are for local/testing only when ENABLE_DEV_TOKEN=true.",
        ],
    )
