from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


DEFAULT_DEV_USER_ID = "550e8400-e29b-41d4-a716-446655440000"


class DevTokenRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_id": DEFAULT_DEV_USER_ID,
                "role": "customer",
                "email": "dev@localhost",
            }
        }
    )

    user_id: UUID | None = Field(
        default=None,
        description="User ID to embed in the token. Defaults to a fixed dev UUID.",
    )
    role: Literal["customer", "provider", "admin"] = Field(
        default="customer",
        description="Role assigned to the dev token.",
    )
    email: EmailStr | None = Field(
        default="dev@localhost",
        description="Email embedded in the token payload.",
    )


class TokenResponse(BaseModel):
    access_token: str = Field(..., description="JWT access token.")
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Token lifetime in seconds.")
    user: dict = Field(..., description="User claims embedded in the token.")


class TokenPayloadResponse(BaseModel):
    id: str
    role: str | None = None
    email: str | None = None
