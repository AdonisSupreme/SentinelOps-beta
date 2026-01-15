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
    """
    Authenticates user via central AD/Auth service,
    then validates presence in SentinelOps DB.

    Returns:
        dict: combined user profile (central + local)
    """

    log.info(f"🔐 Starting authentication flow for user: {email}")

    # 1️⃣ Call central authentication service
    try:
        log.info("➡️ Forwarding credentials to central auth service")

        response = requests.post(
            CENTRAL_AUTH_URL,
            json={
                "email": email,
                "password": password
            },
            timeout=5
        )

    except requests.RequestException as exc:
        log.exception("💥 Central auth service unreachable")
        raise AuthenticationError("Authentication service unavailable") from exc

    # 2️⃣ Handle authentication failure
    if response.status_code == 401:
        log.warning("❌ Central auth rejected credentials")
        raise AuthenticationError("Invalid username or password")

    if response.status_code != 200:
        log.error(f"🚨 Unexpected auth response: {response.status_code}")
        raise AuthenticationError("Authentication failed")

    central_user = response.json()

    log.info("✅ Central authentication successful")

    # Expected central payload fields
    # id, name, email, retry, created_at, updated_at, raw_user

    # 3️⃣ Verify user exists in SentinelOps DB
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, username, is_active, created_at
                FROM users
                WHERE email = %s
                """,
                (email,)
            )
            local_user = cur.fetchone()

    if not local_user:
        log.error("🚫 User authenticated but not registered in SentinelOps")
        raise AuthenticationError("User not authorized for SentinelOps")

    if not local_user[2]:
        log.warning("⛔ User account inactive in SentinelOps")
        raise AuthenticationError("User account disabled")

    log.info("🔓 User authorized in SentinelOps")

    # 4️⃣ Build unified user context
    unified_user = {
        "id": local_user[0],
        "username": local_user[1],
        "email": central_user.get("email"),
        "name": central_user.get("name"),
        "central_id": central_user.get("id"),
        "created_at": local_user[3],
        "raw_user": central_user.get("raw_user"),
    }

    log.info("🧠 Authentication & authorization completed")

    return unified_user

def get_user_from_token(token: str) -> dict:
    """
    Decode the JWT or extract user for frontend.
    For now, returns a dummy user matching the token.
    """
    if not token.startswith("dummy-jwt-for-"):
        raise AuthenticationError("Invalid token")

    username = token.replace("dummy-jwt-for-", "")

    # Fetch user from DB to return latest info
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, is_active, created_at FROM users WHERE username = %s",
                (username,)
            )
            local_user = cur.fetchone()

    if not local_user:
        raise AuthenticationError("User not found")

    user_id = str(local_user[0])  # convert UUID to string
    created_at = local_user[3]
    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at)

    # In a real app, merge with central user if needed
    return {
        "id": user_id,
        "username": local_user[1],
        "email": f"{local_user[1]}@example.com",
        "name": local_user[1].title(),
        "central_id": f"central-{user_id}",
        "created_at": created_at,
        "raw_user": {}
    }
