from pathlib import Path
from typing import List, Any
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Load .env early so both os.getenv(...) and BaseSettings can use it.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=ENV_FILE, override=False)

class Settings(BaseSettings):
    # -----------------------------
    # App Info
    # -----------------------------
    APP_NAME: str = "SentinelOps"
    VERSION: str = "0.2.0"
    ENV: str = "development"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"

    # -----------------------------
    # Database
    # -----------------------------
    DATABASE_URL: str = Field(..., min_length=1)

    # -----------------------------
    # Security / Auth
    # -----------------------------
    SECRET_KEY: str = Field(..., min_length=1)
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # Central Auth Gateway
    CENTRAL_AUTH_URL: str = Field(..., min_length=1)
    FRONTEND_URL: str | None = None

    # -----------------------------
    # CORS
    # -----------------------------
    # Keep as Any to prevent pydantic-settings JSON pre-parse errors from env.
    # We normalize to List[str] in the validator below.
    CORS_ORIGINS: Any = Field(default_factory=list)

    # -----------------------------
    # External Dashboard Sources
    # -----------------------------
    DASHBOARD_WEEKLY_ENDPOINT: str | None = None
    DASHBOARD_PREDICTION_ENDPOINT: str | None = None

    # -----------------------------
    # Email / SMTP
    # -----------------------------
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM: str | None = None
    SMTP_USE_TLS: bool = False
    SMTP_STARTTLS: bool = True

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, value) -> List[str]:
        """Allow CORS_ORIGINS from env as JSON array or comma-separated string."""
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            # JSON array form: ["http://a","http://b"]
            if raw.startswith("["):
                try:
                    import json
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        return [str(v).strip() for v in parsed if str(v).strip()]
                except Exception:
                    pass
            # Comma-separated form: http://a,http://b
            return [item.strip() for item in raw.split(",") if item.strip()]
        return [str(value).strip()] if str(value).strip() else []

    @field_validator("LOG_LEVEL", mode="before")
    @classmethod
    def normalize_log_level(cls, value) -> str:
        return str(value or "INFO").upper()

    # -----------------------------
    # Scheduled Tasks / Maintenance
    # -----------------------------
    NOTIFICATION_CHECK_INTERVAL: int = 60
    CLEANUP_OLD_DATA_DAYS: int = 90
    APPLICATION_TIMEZONE: str = "Africa/Harare"
    TRUSTLINK_SCHEDULE_TIMEZONE: str = "Africa/Harare"
    NETWORK_SENTINEL_ENGINE_RECONCILE_SECONDS: int = 15
    NETWORK_SENTINEL_SAMPLE_INTERVAL_SECONDS: int = 60
    NETWORK_SENTINEL_HOUSEKEEPING_INTERVAL_SECONDS: int = 1800
    NETWORK_SENTINEL_RAW_RETENTION_DAYS: int = 2
    NETWORK_SENTINEL_SAMPLE_RETENTION_DAYS: int = 14
    NETWORK_SENTINEL_EVENT_RETENTION_DAYS: int = 14
    NETWORK_SENTINEL_OUTAGE_RETENTION_DAYS: int = 14
    NETWORK_SENTINEL_PING_EXECUTABLE: str | None = None

    @model_validator(mode="after")
    def validate_runtime_security(self) -> "Settings":
        if self.FRONTEND_URL:
            self.FRONTEND_URL = self.FRONTEND_URL.rstrip("/")
        else:
            frontend_candidates = [
                origin.rstrip("/")
                for origin in self.CORS_ORIGINS
                if isinstance(origin, str) and origin.startswith(("http://", "https://"))
            ]
            if frontend_candidates:
                self.FRONTEND_URL = frontend_candidates[0]
            else:
                raise ValueError("FRONTEND_URL is required in .env or deployment environment.")

        production_envs = {"production", "prod"}
        if self.ENV.strip().lower() in production_envs:
            weak_secret_values = {
                "change-me",
                "changeme",
                "your-secret-key-change-in-production",
            }
            if self.SECRET_KEY.strip() in weak_secret_values or len(self.SECRET_KEY) < 32:
                raise ValueError("SECRET_KEY must be a strong deployment secret in production.")
            if "*" in self.CORS_ORIGINS:
                raise ValueError("CORS_ORIGINS must not contain '*' in production.")
        return self

    # -----------------------------
    # Pydantic BaseSettings Config
    # -----------------------------
    model_config = {
        "env_file": str(ENV_FILE),
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        "extra": "ignore",
    }


# Singleton-style settings object
settings = Settings()
