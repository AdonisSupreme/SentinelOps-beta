import os
from typing import List
from pydantic import field_validator
from pydantic_settings import BaseSettings

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
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000", 
        "http://localhost:8000"
    ]

    # -----------------------------
    # Scheduled Tasks / Maintenance
    # -----------------------------
    NOTIFICATION_CHECK_INTERVAL: int = int(os.getenv("NOTIFICATION_CHECK_INTERVAL", "60"))  # seconds
    CLEANUP_OLD_DATA_DAYS: int = int(os.getenv("CLEANUP_OLD_DATA_DAYS", "90"))

    # -----------------------------
    # Pydantic BaseSettings Config
    # -----------------------------
    model_config = {
        # "env_file": ".env",
        # "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }


# Singleton-style settings object
settings = Settings()
