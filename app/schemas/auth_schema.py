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
