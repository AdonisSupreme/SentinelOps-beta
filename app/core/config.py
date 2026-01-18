import os
from typing import List
from pydantic import field_validator
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # -----------------------------
    # App Info
    # -----------------------------
    APP_NAME: str = "SentinelOps"
    VERSION: str = "0.2.0"
    ENV: str = os.getenv("ENV", "development")  # dev / staging / prod
    DEBUG: bool = os.getenv("DEBUG", "False").lower() == "true"
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", 8000))
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

    # -----------------------------
    # Database
    # -----------------------------
    DATABASE_URL: str = (
    "postgresql://neondb_owner:npg_Xi47aUVlApQR@"
    "ep-curly-smoke-ahieybxi-pooler.c-3.us-east-1.aws.neon.tech/"
    "neondb?sslmode=require&channel_binding=require"
    )

    # -----------------------------
    # Security / Auth
    # -----------------------------
    SECRET_KEY: str = os.getenv(
        "SECRET_KEY", 
        "your-secret-key-change-in-production"
    )
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # Central Auth Gateway
    CENTRAL_AUTH_URL: str = os.getenv(
        "CENTRAL_AUTH_URL",
        "http://192.168.1.106:7000/api/gateway/user-service/login"
    )

    # -----------------------------
    # CORS
    # -----------------------------
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
    ]

    @field_validator("CORS_ORIGINS", mode="before")
    def parse_cors(cls, v):
        """Accept comma-separated string in ENV or JSON array"""
        if isinstance(v, str):
            return [x.strip() for x in v.split(",")]
        return v

    # -----------------------------
    # Scheduled Tasks / Maintenance
    # -----------------------------
    NOTIFICATION_CHECK_INTERVAL: int = 60  # seconds
    CLEANUP_OLD_DATA_DAYS: int = 90

    # -----------------------------
    # Pydantic BaseSettings Config
    # -----------------------------
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }


# Singleton-style settings object
settings = Settings()
