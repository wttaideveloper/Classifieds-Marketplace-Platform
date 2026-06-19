from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ENVIRONMENT: Literal["development", "production"] = "development"
    DATABASE_URL: str
    AUTO_CREATE_TABLES: bool = True
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    FRONTEND_URL: str = "http://localhost:3000"
    CORS_ORIGINS: str = "http://localhost:3000"
    # Allow browser requests from localhost (any port) for local frontend dev.
    CORS_ALLOW_LOCALHOST: bool = True
    # Google OAuth
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    GOOGLE_REDIRECT_URI: str
    # send email
    email_user: str
    email_pass: str

    class Config:
        env_file = ".env"

    @model_validator(mode="after")
    def validate_production_settings(self):
        if self.ENVIRONMENT != "production":
            return self

        if not self.FRONTEND_URL.strip():
            raise ValueError("FRONTEND_URL is required when ENVIRONMENT=production")

        if not self.CORS_ORIGINS.strip():
            raise ValueError("CORS_ORIGINS is required when ENVIRONMENT=production")

        localhost_markers = ("localhost", "127.0.0.1")
        if any(marker in self.FRONTEND_URL for marker in localhost_markers):
            raise ValueError(
                "FRONTEND_URL must be a production URL when ENVIRONMENT=production"
            )

        return self

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def cors_origins_list(self) -> list[str]:
        if self.CORS_ORIGINS.strip() == "*":
            return ["*"]

        origins: set[str] = set()
        for origin in self.CORS_ORIGINS.split(","):
            value = origin.strip().rstrip("/")
            if value:
                origins.add(value)

        frontend_url = self.FRONTEND_URL.strip().rstrip("/")
        if frontend_url:
            origins.add(frontend_url)

        if self.CORS_ALLOW_LOCALHOST:
            origins.update(
                {
                    "http://localhost:3000",
                    "http://127.0.0.1:3000",
                }
            )

        return sorted(origins)

    @property
    def cors_origin_regex(self) -> str | None:
        if not self.CORS_ALLOW_LOCALHOST:
            return None
        return r"https?://(localhost|127\.0\.0\.1)(:\d+)?$"


settings = Settings()
