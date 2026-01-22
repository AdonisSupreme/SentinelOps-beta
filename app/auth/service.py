import requests
from app.db.database import get_connection
from app.core.logging import get_logger
from app.core.security import verify_and_decode_token
from datetime import datetime, timezone
from uuid import UUID
import jwt

log = get_logger("auth-service")

# 🔐 Dummy central auth endpoint (replace later)
CENTRAL_AUTH_URL = "http://192.168.1.106:7000/api/gateway/user-service/login"

# Session lifetime (authoritative, separate from JWT)
SESSION_EXPIRE_HOURS = 24

class AuthenticationError(Exception):
    pass


def authenticate_user(email: str, password: str, request=None) -> dict:
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
        raise AuthenticationError("User not found")

    if not row[5]:
        raise AuthenticationError("User account disabled")

    user_id = str(row[0])

    # 3️⃣ Reuse active session on login (with IP + User-Agent check)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, expires_at, ip_address, user_agent
                FROM auth_sessions
                WHERE user_id = %s
                  AND revoked_at IS NULL
                  AND expires_at > NOW()
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id,)
            )
            existing = cur.fetchone()

    if existing:
        session_id, expires_at, stored_ip, stored_ua = existing
        # Compare IP and User-Agent
        request_ip = request.client.host if request else "unknown"
        request_ua = request.headers.get("user-agent", "unknown") if request else "unknown"
        if request_ip == stored_ip and request_ua == stored_ua:
            session_id = str(session_id)
        else:
            # Mismatch: revoke old session and create new
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE auth_sessions SET revoked_at = NOW() WHERE id = %s",
                        (str(session_id),)
                    )
                    conn.commit()
            # Fall through to create new session below
    else:
        # Create new session (24h)
        from uuid import uuid4
        session_id = str(uuid4())
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO auth_sessions 
                    (id, user_id, expires_at, created_at)
                    VALUES (%s, %s, NOW() + INTERVAL '24 hours', NOW())
                    """,
                    (session_id, user_id)
                )
                conn.commit()

    return {
        "id": user_id,
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
    """
    Verify JWT and validate session, then return user.
    
    Raises:
        AuthenticationError if token invalid, expired, or session revoked
    
    Returns:
        User dict with id, username, email, etc.
    """
    try:
        # Verify JWT signature and decode
        payload = verify_and_decode_token(token)
    except jwt.ExpiredSignatureError:
        raise AuthenticationError("Token expired")
    except (jwt.InvalidSignatureError, jwt.DecodeError):
        raise AuthenticationError("Invalid token")
    
    user_id = payload.get("sub")
    session_id = payload.get("sid")
    
    if not user_id or not session_id:
        raise AuthenticationError("Token missing required claims")
    
    # Validate session exists and is not revoked
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Check session
            cur.execute(
                """
                SELECT 
                    id, 
                    user_id, 
                    expires_at, 
                    revoked_at
                FROM auth_sessions
                WHERE id = %s AND user_id = %s
                """,
                (session_id, user_id)
            )
            session_row = cur.fetchone()
            
            if not session_row:
                raise AuthenticationError("Session not found")
            
            session_id_val, session_user_id, expires_at, revoked_at = session_row
            
            # Check if revoked
            if revoked_at is not None:
                raise AuthenticationError("Session revoked")
            
            # Check if expired using DB time
            cur.execute("SELECT now() AT TIME ZONE 'UTC'")
            db_now = cur.fetchone()[0]
            if expires_at and (db_now - expires_at).total_seconds() >= 0:
                raise AuthenticationError("Session expired")
            
            # Now get user details
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
                WHERE u.id = %s
                LIMIT 1
                """,
                (user_id,)
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
