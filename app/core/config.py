from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings

# Project root (directory that contains `app/`), independent of process CWD.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    ENVIRONMENT: Literal["development", "production"] = "development"
    # Root logger level. Nothing in this app calls logging.basicConfig, so
    # without this, Python's root logger defaults to WARNING and every
    # logger.info(...) call anywhere in the codebase is silently dropped —
    # including diagnostic logging added specifically to debug production
    # issues. Override via env var if a deployment wants less verbosity.
    LOG_LEVEL: str = "INFO"
    DATABASE_URL: str
    AUTO_CREATE_TABLES: bool = True
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    CHAT_TOKEN_EXPIRE_SECONDS: int = 300
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
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    # Chat file uploads — relative paths resolve under the project root (e.g. /app/uploads in Docker).
    # Override with an absolute path when mounting a host volume (e.g. UPLOAD_DIR=/app/uploads).
    UPLOAD_DIR: str = "uploads"
    MAX_IMAGE_SIZE_MB: int = 10
    MAX_DOCUMENT_SIZE_MB: int = 25
    MAX_AUDIO_SIZE_MB: int = 25
    MAX_VIDEO_SIZE_MB: int = 300
    # Images (training media, eBook pictures): the longest side and the total pixels are capped so a small file cannot unpack into a huge bitmap.
    MAX_IMAGE_DIMENSION: int = 8000
    MAX_IMAGE_PIXELS: int = 40_000_000
    # Virus scanning of uploaded images through a ClamAV daemon (clamd). Empty host = not scanned. With CLAMAV_REQUIRED an upload is
    # refused when the scanner cannot be reached; otherwise it is accepted and marked "unscanned".
    CLAMAV_HOST: str = ""
    CLAMAV_PORT: int = 3310
    CLAMAV_REQUIRED: bool = False
    # eBook limits
    EBOOK_MAX_PAGES: int = 500
    EBOOK_MAX_PAGE_HTML_BYTES: int = 200_000
    EBOOK_MAX_IMAGES_PER_PAGE: int = 30
    # Dev-only: used when no JWT is sent (ENVIRONMENT=development)
    DEV_DEFAULT_USER_ID: str = "550e8400-e29b-41d4-a716-446655440000"
    DEV_DEFAULT_USER_ROLE: str = "admin"
    # Allow /auth/dev-token on staging/production test servers
    ENABLE_DEV_TOKEN: bool = False
    # Socket.IO mount path. Production same-host deployments use /api/socket.io.
    # Set SOCKETIO_STANDALONE=true when the API has its own domain and /socket.io is proxied at root.
    SOCKETIO_PATH: str = "/socket.io"
    SOCKETIO_STANDALONE: bool = False
    PUBLIC_API_BASE_URL: str = ""
    # Optional Redis URL — for split deploy / multiple socket server instances (event pub/sub).
    # Does NOT share Engine.IO polling sessions between Gunicorn workers on the same port.
    SOCKETIO_REDIS_URL: str = ""
    # Gunicorn workers for combined socket_app entrypoint. Must stay 1 for polling to work.
    WEB_CONCURRENCY: int = 1
    SOCKET_WORKERS: int = 1
    # Grace period (seconds) before a user with zero active sockets is marked
    # offline — absorbs reconnects/refreshes/transport upgrades without a
    # false user_offline. See app/services/presence_service.py.
    PRESENCE_OFFLINE_GRACE_SECONDS: float = 4.0
    # Speech-to-text (OpenAI Whisper). Set OPENAI_API_KEY to enable transcription.
    OPENAI_API_KEY: str = ""
    SPEECH_TO_TEXT_MODEL: str = "whisper-1"
    # Push notifications — Firebase Cloud Messaging (FCM)
    FIREBASE_CREDENTIALS_PATH: str = ""
    FIREBASE_CREDENTIALS_JSON: str = ""
    # SMS — Bravo provider
    BRAVO_SMS_API_URL: str = ""
    BRAVO_API_KEY: str = ""
    BRAVO_SMS_TIMEOUT_SECONDS: int = 15
    # Keycloak / Invigorate login tokens (RS256)
    KEYCLOAK_ISSUER: str = ""
    KEYCLOAK_AUDIENCE: str = ""
    KEYCLOAK_JWKS_URL: str = ""
    # Name of the HttpOnly cookie set by the Web login flow.  The cookie value
    # is a Keycloak access token and is accepted by REST and Socket.IO in
    # addition to Authorization: Bearer for mobile/API clients.
    WEB_SESSION_COOKIE_NAME: str = "access_token"
    # Comma-separated additional cookie names to check (in order) when
    # WEB_SESSION_COOKIE_NAME is absent — covers a WebAuth provider issuing
    # the session under a different cookie name (e.g. a rename/migration).
    WEB_SESSION_COOKIE_FALLBACK_NAMES: str = "he_session"
    # Invigorate Auth internal API (tenant user lookup for bulk notifications)
    INVIGORATE_AUTH_BASE_URL: str = "https://admin.apis.invigor8.app"
    INVIGORATE_INTERNAL_API_KEY: str = ""
    # Base URL of the Invigorate admin API that serves GET /api/v1/tenant/members.
    # Empty = fall back to INVIGORATE_AUTH_BASE_URL.
    INVIGORATE_ADMIN_API_BASE_URL: str = ""
    # Celery / Redis background jobs
    CELERY_BROKER_URL: str = ""
    CELERY_RESULT_BACKEND: str = ""
    # Training day-before / final-day reminders run on an in-process thread (no Celery worker or
    # beat is deployed). Unset = on in production, off in development; set explicitly to override.
    TRAINING_REMINDERS_ENABLED: bool | None = None
    TRAINING_REMINDER_INTERVAL_SECONDS: int = 900
    # "Complete your training before the deadline" reminders go to learners who have not finished, this many days before their
    # deadline (the training's end date, or the end of their access if that comes first). Comma separated; empty switches them off.
    TRAINING_DEADLINE_REMINDER_DAYS: str = "7,3,1"
    # Public HTTPS origin used for every file/media link the API returns, e.g. https://chat.wisdomtooth.tech.
    # Mobile blocks cleartext http:// media, and links already stored with a bare-IP http:// host
    # (http://13.207.85.164/api/v1/trainings/upload/...) are rewritten to this origin when served.
    # Falls back to PUBLIC_API_BASE_URL when that one is already an https hostname.
    PUBLIC_MEDIA_BASE_URL: str = ""
    # Rewrite http:// links to https:// in Training API responses and when saving. Unset = on in
    # production, off in development (local servers have no TLS).
    FORCE_HTTPS_MEDIA_URLS: bool | None = None
    # GET /products and /services answer 403 "Catalog access denied" to a logged-in user whose token and
    # /auth/me profile carry no role the catalog recognises, while an anonymous visitor can browse the same
    # list. Off by default (that denial is a deliberate, tested policy). Set true to let such a user browse
    # read-only like an anonymous visitor; writes still need a real role.
    CATALOG_UNRECOGNIZED_ROLE_READ_ONLY: bool = False

    # --- Group chat (docs/group-chat-api.md). Starting values from the product decisions; all are settings so
    # they can change without a release. Items marked "confirm" are waiting on legal / compliance.
    GROUP_MAX_MEMBERS_CARE: int = 15  # confirm
    GROUP_MAX_MEMBERS_PROGRAM: int = 50  # confirm
    GROUP_MAX_ADMINS: int = 3
    # Training groups: a Provider may create / manage a group for a training only when they are its assigned instructor
    # (Training.instructor_id). Set to false to let every verified Provider of the clinic use every training of the
    # clinic. Enterprise Admins always can, for the trainings of their own clinic.
    GROUP_PROVIDER_REQUIRES_TRAINING_ASSIGNMENT: bool = True
    GROUP_EDIT_WINDOW_MINUTES: int = 15  # confirm
    # Only a provider listed as verified (POST /groups/verified-providers) can create or own a group.
    GROUP_REQUIRE_VERIFIED_PROVIDER: bool = True
    # Whether patients may post to each other in a program group at all (confirm). When false only providers can
    # post there, whatever the admins-only switch says.
    GROUP_PROGRAM_PATIENTS_CAN_POST: bool = True
    # Reactions: never in a care group; in a program group only these.
    GROUP_PROGRAM_REACTIONS: str = "👍,❤️,🙏,👏,😊"
    # Comma-separated words that may not appear in a group name (health conditions). A patient's own name is
    # always refused as well.
    GROUP_NAME_BLOCKED_TERMS: str = ""
    GROUP_EMERGENCY_NUMBER: str = "112"
    # Days the record is kept after a group closes (confirm with legal). Nothing is ever deleted by the API; this is
    # the date shown on the group and used by whatever job later purges.
    GROUP_RETENTION_DAYS: int | None = None
    GROUP_GRIEVANCE_OFFICER_NAME: str = ""
    GROUP_GRIEVANCE_OFFICER_EMAIL: str = ""
    GROUP_GRIEVANCE_OFFICER_PHONE: str = ""

    # --- Payments, multi-currency and tax (docs/payments-api.md)
    # Gateway that takes the card / bank payment: test (no money moves; for development and QA), razorpay or stripe.
    PAYMENT_GATEWAY: Literal["test", "razorpay", "stripe"] = "test"
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_WEBHOOK_SECRET: str = ""
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    # Signs the test gateway's webhooks; only used while PAYMENT_GATEWAY=test.
    TEST_GATEWAY_WEBHOOK_SECRET: str = "test-webhook-secret"
    # The test gateway moves no money and anyone can complete a payment on it, so it is refused when ENVIRONMENT=production
    # unless this is set (for a staging server that runs in production mode).
    PAYMENT_ALLOW_TEST_GATEWAY: bool = False
    # The currency the books are kept in. Fixed for the organisation: change it only with a finance sign-off.
    PAYMENT_BASE_CURRENCY: str = "INR"
    PAYMENT_GATEWAY_TIMEOUT_SECONDS: int = 20
    # A payment still waiting this long for the gateway is checked against it (and expired when the gateway never saw it).
    PAYMENT_STALE_MINUTES: int = 30
    PAYMENT_MAX_ATTEMPTS: int = 5
    # Risk check: anything at or above REVIEW is held for a person, at or above DENY is refused.
    PAYMENT_RISK_REVIEW_SCORE: int = 50
    PAYMENT_RISK_DENY_SCORE: int = 80
    PAYMENT_RISK_HIGH_AMOUNT_BASE: float = 200000.0
    # Optional outside fraud-scoring service. It is sent the payment's signals and answers {"score": 0-100, "reasons": [...]}.
    # Empty = only the built-in rules. If it does not answer within 3 seconds the built-in rules decide alone.
    FRAUD_SERVICE_URL: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""
    # Refund target turnaround after approval; a refund still open after this is escalated to the Super Admins.
    REFUND_SLA_HOURS: int = 72
    # A timed quiz that is auto-submitted when the countdown ends reaches the server a little late; this much lateness is accepted.
    ASSESSMENT_SUBMIT_GRACE_SECONDS: int = 60

    # --- Virtual meeting providers (docs/meetings-api.md). Each business connects its own account over OAuth; these identify OUR app
    # to the provider. A provider with no client id is "not configured" and cannot be connected. The Google client here is for
    # Calendar / Meet and must be a different OAuth client from the sign-in one (GOOGLE_CLIENT_ID).
    ZOOM_CLIENT_ID: str = ""
    ZOOM_CLIENT_SECRET: str = ""
    GOOGLE_MEET_CLIENT_ID: str = ""
    GOOGLE_MEET_CLIENT_SECRET: str = ""
    MICROSOFT_CLIENT_ID: str = ""
    MICROSOFT_CLIENT_SECRET: str = ""
    MICROSOFT_TENANT: str = "common"
    # Key that encrypts stored provider tokens (any long random text). Falls back to a key derived from SECRET_KEY, but set its own
    # so that rotating SECRET_KEY does not make every connection unreadable.
    MEETING_TOKEN_ENCRYPTION_KEY: str = ""
    # Public base of THIS API, used to build the OAuth redirect address registered at each provider
    # (<base>/api/v1/meetings/oauth/callback/<provider>). Empty = PUBLIC_API_BASE_URL, then the request's own address.
    MEETING_OAUTH_REDIRECT_BASE: str = ""
    # Minutes before the start from which the Join button is active for a customer.
    MEETING_JOIN_OPENS_MINUTES_BEFORE: int = 15
    MEETING_GENERATION_MAX_ATTEMPTS: int = 5
    MEETING_PROVIDER_TIMEOUT_SECONDS: int = 20
    MEETING_JOBS_ENABLED: bool | None = None
    MEETING_JOBS_INTERVAL_SECONDS: int = 600
    # Platform commission and gateway fee estimate used at settlement (a merchant's own commission overrides it).
    PLATFORM_COMMISSION_PERCENT: float = 10.0
    GATEWAY_FEE_PERCENT: float = 2.0
    GATEWAY_FEE_FIXED_BASE: float = 0.0
    # Where automatic exchange rates come from (Frankfurter / ECB reference rates, no key needed).
    EXCHANGE_RATE_API_URL: str = "https://api.frankfurter.app/latest"
    # Rates older than this are refused for a payment (the customer sees "rates unavailable" instead of a stale price).
    EXCHANGE_RATE_MAX_AGE_HOURS: int = 48
    # The background jobs (refresh rates, check stale payments, refund SLA, due settlements, retry reminders) run on an
    # in-process thread like the training reminders. Unset = on in production, off in development.
    PAYMENT_JOBS_ENABLED: bool | None = None
    # A GSTIN / VAT number with a valid format is approved at once (reverse charge applies) instead of waiting for a Super Admin.
    # Off by default: a format check does not prove the business is registered.
    TAX_ID_AUTO_APPROVE_VALID_FORMAT: bool = False
    PAYMENT_JOBS_INTERVAL_SECONDS: int = 600

    class Config:
        env_file = ".env"

    @property
    def group_program_reactions(self) -> list[str]:
        return [e.strip() for e in self.GROUP_PROGRAM_REACTIONS.split(",") if e.strip()]

    @property
    def group_blocked_name_terms(self) -> list[str]:
        return [t.strip().lower() for t in self.GROUP_NAME_BLOCKED_TERMS.split(",") if t.strip()]

    @property
    def force_https_media_urls(self) -> bool:
        if self.FORCE_HTTPS_MEDIA_URLS is not None:
            return self.FORCE_HTTPS_MEDIA_URLS
        return self.ENVIRONMENT == "production"

    @property
    def training_reminders_enabled(self) -> bool:
        if self.TRAINING_REMINDERS_ENABLED is not None:
            return self.TRAINING_REMINDERS_ENABLED
        return self.ENVIRONMENT == "production"

    @property
    def payment_jobs_enabled(self) -> bool:
        if self.PAYMENT_JOBS_ENABLED is not None:
            return self.PAYMENT_JOBS_ENABLED
        return self.ENVIRONMENT == "production"

    @property
    def celery_broker_url(self) -> str:
        if self.CELERY_BROKER_URL.strip():
            return self.CELERY_BROKER_URL.strip()
        if self.SOCKETIO_REDIS_URL.strip():
            return self.SOCKETIO_REDIS_URL.strip()
        return "redis://localhost:6379/0"

    @property
    def celery_result_backend(self) -> str:
        return self.CELERY_RESULT_BACKEND.strip() or self.celery_broker_url

    @property
    def web_session_cookie_names(self) -> list[str]:
        """Ordered cookie names to check for the WebAuth session token: the
        configured primary name first, then WEB_SESSION_COOKIE_FALLBACK_NAMES
        (comma separated) for a provider issuing the session under a
        different cookie name."""
        names = [self.WEB_SESSION_COOKIE_NAME]
        names += [
            name.strip()
            for name in self.WEB_SESSION_COOKIE_FALLBACK_NAMES.split(",")
            if name.strip() and name.strip() != self.WEB_SESSION_COOKIE_NAME
        ]
        return names

    @property
    def invigorate_admin_api_base_url(self) -> str:
        return (self.INVIGORATE_ADMIN_API_BASE_URL.strip() or self.INVIGORATE_AUTH_BASE_URL.strip()).rstrip("/")

    @property
    def invigorate_internal_api_configured(self) -> bool:
        return bool(
            self.INVIGORATE_AUTH_BASE_URL.strip() and self.INVIGORATE_INTERNAL_API_KEY.strip()
        )

    @property
    def celery_configured(self) -> bool:
        return bool(self.celery_broker_url)

    @property
    def speech_to_text_enabled(self) -> bool:
        return bool(self.OPENAI_API_KEY.strip())

    @property
    def firebase_configured(self) -> bool:
        return bool(
            self.FIREBASE_CREDENTIALS_PATH.strip() or self.FIREBASE_CREDENTIALS_JSON.strip()
        )

    @property
    def bravo_sms_configured(self) -> bool:
        return bool(self.BRAVO_SMS_API_URL.strip() and self.BRAVO_API_KEY.strip())

    @property
    def keycloak_configured(self) -> bool:
        return bool(self.KEYCLOAK_ISSUER.strip())

    @property
    def keycloak_jwks_url(self) -> str:
        if self.KEYCLOAK_JWKS_URL.strip():
            return self.KEYCLOAK_JWKS_URL.strip()
        issuer = self.KEYCLOAK_ISSUER.rstrip("/")
        return f"{issuer}/protocol/openid-connect/certs"

    @staticmethod
    def normalize_socketio_path(path: str) -> str:
        normalized = (path or "/socket.io").strip()
        if not normalized.startswith("/"):
            normalized = f"/{normalized}"
        return normalized.rstrip("/") or "/socket.io"

    @model_validator(mode="after")
    def resolve_socketio_path(self):
        if (
            self.ENVIRONMENT == "production"
            and not self.SOCKETIO_STANDALONE
            and self.normalize_socketio_path(self.SOCKETIO_PATH) == "/socket.io"
        ):
            self.SOCKETIO_PATH = "/api/socket.io"
        else:
            self.SOCKETIO_PATH = self.normalize_socketio_path(self.SOCKETIO_PATH)
        return self

    @model_validator(mode="after")
    def validate_socketio_worker_config(self):
        if self.SOCKET_WORKERS > 1:
            raise ValueError(
                "SOCKET_WORKERS must be 1 for combined Socket.IO deployment. "
                "Engine.IO polling sessions are stored in memory per worker — Redis does not fix "
                "this on a single Gunicorn port. Use SOCKET_WORKERS=1, or run "
                "split mode: scripts/entrypoint-api.sh (port 8000) + entrypoint-socket.sh (port 8001)."
            )
        return self

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
    def upload_dir_path(self) -> Path:
        """Absolute path where chat attachments are stored (always writable target for the app)."""
        path = Path(self.UPLOAD_DIR)
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
        return path.resolve()

    @property
    def cors_origins_list(self) -> list[str]:
        if self.CORS_ORIGINS.strip() == "*":
            # With allow_credentials=True, browsers reject "*". Return empty and rely on explicit origins.
            return []

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
                    "http://localhost:3001",
                    "http://127.0.0.1:3001",
                    "http://localhost:3002",
                    "http://127.0.0.1:3002",
                }
            )

        return sorted(origins)

    @property
    def cors_origin_regex(self) -> str | None:
        if not self.CORS_ALLOW_LOCALHOST:
            return None
        return r"https?://(localhost|127\.0\.0\.1)(:\d+)?$"


settings = Settings()
