import requests
from app.db.database import get_connection
from app.core.logging import get_logger
from datetime import datetime
from uuid import UUID

log = get_logger("auth-service")

# 🔐 Dummy central auth endpoint (replace later)
CENTRAL_AUTH_URL = "http://192.168.1.106:7000/api/gateway/user-service/login"

class AuthenticationError(Exception):
    pass


def authenticate_user(email: str, password: str) -> dict:
    log.info(f"🔐 Starting authentication flow for user: {email}")

    # 1️⃣ Central authentication
    try:
        response = requests.post(
            CENTRAL_AUTH_URL,
            json={"email": email, "password": password},
            timeout=5
        )
    except requests.RequestException as exc:
        raise AuthenticationError("Authentication service unavailable") from exc

    if response.status_code == 401:
        raise AuthenticationError("Invalid username or password")

    if response.status_code != 200:
        raise AuthenticationError("Authentication failed")

    central_user = response.json()

    # 2️⃣ Sentinel user + role
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    u.id,
                    u.username,
                    u.email,
                    u.first_name,
                    u.last_name,
                    u.is_active,
                    u.created_at,
                    r.name AS role
                FROM users u
                JOIN user_roles ur ON ur.user_id = u.id
                JOIN roles r ON r.id = ur.role_id
                WHERE u.email = %s
                LIMIT 1
                """,
                (email,)
            )
            row = cur.fetchone()

    if not row:
        raise AuthenticationError("User not authorized for SentinelOps")

    if not row[5]:
        raise AuthenticationError("User account disabled")

    return {
        "id": str(row[0]),
        "username": row[1],
        "email": row[2],
        "first_name": row[3],
        "last_name": row[4],
        "role": row[7],
        "central_id": str(central_user.get("id")),
        "created_at": row[6],
        "raw_user": central_user.get("raw_user"),
    }

def get_user_from_token(token: str) -> dict:
    if not token.startswith("dummy-jwt-for-"):
        raise AuthenticationError("Invalid token")

    username = token.replace("dummy-jwt-for-", "")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    u.id,
                    u.username,
                    u.email,
                    u.first_name,
                    u.last_name,
                    u.is_active,
                    u.created_at,
                    r.name AS role
                FROM users u
                JOIN user_roles ur ON ur.user_id = u.id
                JOIN roles r ON r.id = ur.role_id
                WHERE u.username = %s
                LIMIT 1
                """,
                (username,)
            )
            row = cur.fetchone()

    if not row:
        raise AuthenticationError("User not found")

    return {
        "id": str(row[0]),
        "username": row[1],
        "email": row[2],
        "first_name": row[3],
        "last_name": row[4],
        "role": row[7],
        "central_id": f"central-{row[0]}",
        "created_at": row[6],
        "raw_user": {},
    }


# Dependency for FastAPI routes: resolve current user from Authorization header
from fastapi import Header, HTTPException


async def get_current_user(authorization: str = Header(None)) -> dict:
    """FastAPI dependency that returns the current user based on Bearer token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")

    token = authorization.split(" ")[1]

    try:
        user = get_user_from_token(token)
        return user
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
