"""Settings, all from environment variables (see .env.example). Nothing in here depends on the app that calls the service."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "sqlite:///./notifications.db"
    # Producers (the modules or apps that send events): "name:key,name:key". Each caller has its own key, so one can be revoked alone.
    PRODUCER_KEYS: str = ""
    # The key for the admin routes (templates, event types, nudge rules, delivery log).
    ADMIN_KEY: str = ""

    # Delivery
    WORKER_ENABLED: bool = True
    WORKER_INTERVAL_SECONDS: float = 2.0
    MAX_ATTEMPTS: int = 5
    RETRY_BASE_SECONDS: int = 30                      # waits 30s, 60s, 120s ... between attempts
    BATCH_SIZE: int = 100

    # Push (Firebase Cloud Messaging). Leave empty to only log pushes.
    FIREBASE_CREDENTIALS_JSON: str = ""
    FIREBASE_CREDENTIALS_PATH: str = ""

    # Email (SMTP). Leave SMTP_HOST empty to only log emails.
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "no-reply@example.com"
    SMTP_STARTTLS: bool = True

    # Where the service finds the email address of a user when the caller did not send one.
    # Left empty: callers send `email` with the recipient.

    @property
    def producers(self) -> dict[str, str]:
        out = {}
        for part in self.PRODUCER_KEYS.split(","):
            name, _, key = part.strip().partition(":")
            if name and key:
                out[key] = name
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()
