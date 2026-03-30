import os
from pathlib import Path
from typing import List, Any
from pydantic import field_validator
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
    APP_NAME: str = os.getenv("APP_NAME", "SentinelOps")
    VERSION: str = os.getenv("VERSION", "0.2.0")
    ENV: str = os.getenv("ENV", "development")  # dev / staging / prod
    DEBUG: bool = os.getenv("DEBUG", "False").lower() == "true"
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

    # -----------------------------
    # Database
    # -----------------------------
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:echo@localhost:5432/neondb"
    )

    # -----------------------------
    # Security / Auth
    # -----------------------------
    SECRET_KEY: str = os.getenv(
        "SECRET_KEY", 
        "your-secret-key-change-in-production"
    )
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))

    # Central Auth Gateway
    CENTRAL_AUTH_URL: str = os.getenv(
        "CENTRAL_AUTH_URL",
        "http://192.168.1.106:7000/api/gateway/user-service/login"
    )

    # -----------------------------
    # CORS
    # -----------------------------
    # Keep as Any to prevent pydantic-settings JSON pre-parse errors from env.
    # We normalize to List[str] in the validator below.
    CORS_ORIGINS: Any = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:8000",
    )

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

    # -----------------------------
    # Scheduled Tasks / Maintenance
    # -----------------------------
    NOTIFICATION_CHECK_INTERVAL: int = int(os.getenv("NOTIFICATION_CHECK_INTERVAL", "60"))  # seconds
    CLEANUP_OLD_DATA_DAYS: int = int(os.getenv("CLEANUP_OLD_DATA_DAYS", "90"))
    TRUSTLINK_SCHEDULE_TIMEZONE: str = os.getenv("TRUSTLINK_SCHEDULE_TIMEZONE", "Africa/Harare")

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
