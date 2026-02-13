import requests
from app.db.database import get_connection
from app.core.logging import get_logger
from app.core.security import verify_and_decode_token, verify_password
from app.core.config import settings
from datetime import datetime, timezone
from uuid import UUID
import jwt

log = get_logger("auth-service")

# Session lifetime (authoritative, separate from JWT)
SESSION_EXPIRE_HOURS = 24

class AuthenticationError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 401,
        context: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.context = context


def check_ad_status() -> dict:
    checked_at = datetime.now(timezone.utc).isoformat() + "Z"
    # AD integration temporarily disabled for Sentinel-only authentication.
    # TODO: Re-enable AD health probe when integration is ready.
    return {
        "available": False,
        "source": "active_directory",
        "checked_at": checked_at,
        "reason": "AD integration disabled",
    }


def authenticate_with_ad(email: str, password: str) -> dict:
    # AD integration temporarily disabled.
    # TODO: Implement AD authentication flow once integration is ready.
    raise AuthenticationError(
        code="AD_UNAVAILABLE",
        message="Active Directory authentication is currently unavailable",
        status_code=503,
    )


def get_sentinel_user(email: str) -> tuple:
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
                    r.name AS role,
                    u.password_hash
                FROM users u
                JOIN user_roles ur ON ur.user_id = u.id
                JOIN roles r ON r.id = ur.role_id
                WHERE u.email = %s
                LIMIT 1
                """,
                (email,)
            )
            row = cur.fetchone()
    return row


def authenticate_user(email: str, password: str, request=None) -> tuple[dict, str]:
    log.info(f"🔐 Starting authentication flow for user: {email}")

    ad_status = check_ad_status()
    auth_source = "sentinel"

    central_user = None
    # AD integration temporarily disabled.
    # if ad_status["available"]:
    #     central_user = authenticate_with_ad(email, password)

    row = get_sentinel_user(email)

    if not row:
        if auth_source == "sentinel":
            raise AuthenticationError(
                code="INVALID_CREDENTIALS",
                message="Invalid email or password",
                status_code=401,
            )
        raise AuthenticationError(
            code="USER_NOT_FOUND",
            message="User not found",
            status_code=401,
        )

    if not row[5]:
        raise AuthenticationError(
            code="USER_DISABLED",
            message="User account disabled",
            status_code=403,
        )

    if auth_source == "sentinel":
        password_hash = row[8]
        if not verify_password(password, password_hash):
            raise AuthenticationError(
                code="INVALID_CREDENTIALS",
                message="Invalid email or password",
                status_code=401,
            )

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
                  AND expires_at > (NOW() AT TIME ZONE 'UTC')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id,)
            )
            existing = cur.fetchone()

    if existing:
        session_id, expires_at, stored_ip, stored_ua = existing
        print(f"AUTH_SERVICE: Found existing session {session_id} for user {user_id}")
        # Compare IP and User-Agent
        request_ip = request.client.host if request else "unknown"
        request_ua = request.headers.get("user-agent", "unknown") if request else "unknown"
        if request_ip == stored_ip and request_ua == stored_ua:
            session_id = str(session_id)
            print(f"AUTH_SERVICE: Reusing existing session {session_id}")
        else:
            print(f"AUTH_SERVICE: IP/UA mismatch, revoking old session and creating new")
            # Mismatch: revoke old session and create new
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE auth_sessions SET revoked_at = NOW() WHERE id = %s",
                        (str(session_id),)
                    )
                    conn.commit()
            # Create new session after revoking old one
            from uuid import uuid4
            session_id = str(uuid4())
            print(f"AUTH_SERVICE: Creating new session {session_id} for user {user_id}")
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO auth_sessions 
                        (id, user_id, expires_at, created_at)
                        VALUES (%s, %s, ((NOW() AT TIME ZONE 'UTC') + INTERVAL '24 hours') AT TIME ZONE 'UTC', NOW() AT TIME ZONE 'UTC')
                        """,
                        (session_id, user_id)
                    )
                    conn.commit()
                    print(f"AUTH_SERVICE: New session {session_id} created successfully")
    else:
        # Create new session (24h)
        from uuid import uuid4
        session_id = str(uuid4())
        print(f"AUTH_SERVICE: Creating new session {session_id} for user {user_id}")
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO auth_sessions 
                    (id, user_id, expires_at, created_at)
                    VALUES (%s, %s, ((NOW() AT TIME ZONE 'UTC') + INTERVAL '24 hours') AT TIME ZONE 'UTC', NOW() AT TIME ZONE 'UTC')
                    """,
                    (session_id, user_id)
                )
                conn.commit()
                print(f"AUTH_SERVICE: Session {session_id} created successfully")

    user = {
        "id": user_id,
        "username": row[1],
        "email": row[2],
        "first_name": row[3],
        "last_name": row[4],
        "role": row[7],
        "central_id": str(central_user.get("id")) if central_user else "sentinel-local",
        "created_at": row[6],
        "raw_user": central_user.get("raw_user") if central_user else {},
        "session_id": session_id  # Add session_id to user object
    }

    print(f"AUTH_SERVICE: Returning user with session_id: {session_id}")
    return user, auth_source


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
        log.warning("🔐 Token expired")
        raise AuthenticationError(
            code="TOKEN_EXPIRED",
            message="Token expired",
            status_code=401,
        )
    except (jwt.InvalidSignatureError, jwt.DecodeError) as exc:
        log.warning(f"🔐 Token invalid: {exc}")
        raise AuthenticationError(
            code="TOKEN_INVALID",
            message="Invalid token",
            status_code=401,
        )
    
    user_id = payload.get("sub")
    session_id = payload.get("sid")
    
    log.info(f"🔐 Token decoded: user_id={user_id}, session_id={session_id}")
    
    if not user_id or not session_id:
        log.warning("🔐 Token missing required claims")
        raise AuthenticationError(
            code="TOKEN_MISSING_CLAIMS",
            message="Token missing required claims",
            status_code=401,
        )
    
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
                log.warning(f"🔐 Session not found for session_id={session_id}, user_id={user_id}")
                raise AuthenticationError(
                    code="SESSION_NOT_FOUND",
                    message="Session not found",
                    status_code=401,
                )
            
            session_id_val, session_user_id, expires_at, revoked_at = session_row
            log.info(f"🔐 Session found: expires_at={expires_at}, revoked_at={revoked_at}")
            
            # Check if revoked
            if revoked_at is not None:
                log.warning("🔐 Session revoked")
                raise AuthenticationError(
                    code="SESSION_REVOKED",
                    message="Session revoked",
                    status_code=401,
                )
            
            # Check if expired using UTC time
            cur.execute("SELECT (now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'")
            db_now_utc = cur.fetchone()[0]
            
            # Ensure both times are in UTC for comparison
            if expires_at and expires_at.tzinfo is None:
                expires_at_utc = expires_at.replace(tzinfo=timezone.utc)
            else:
                expires_at_utc = expires_at
            
            # Verification logging for session validation
            log.info(f"SESSION TIME CHECK | now_utc={db_now_utc} | expires_at_utc={expires_at_utc}")
                
            # Compare UTC times
            if expires_at_utc and db_now_utc >= expires_at_utc:
                log.warning(f"🔐 Session expired: db_now_utc={db_now_utc}, expires_at_utc={expires_at_utc}")
                raise AuthenticationError(
                    code="SESSION_EXPIRED",
                    message="Session expired",
                    status_code=401,
                )
            
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
                    r.name AS role,
                    u.department_id,
                    u.section_id
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
        log.warning(f"🔐 User not found for user_id={user_id}")
        raise AuthenticationError(
            code="USER_NOT_FOUND",
            message="User not found",
            status_code=401,
        )
    
    log.info(f"🔐 User found: id={row[0]}, username={row[1]}, email={row[2]}, role={row[7]}")
    
    return {
        "id": str(row[0]),
        "username": row[1],
        "email": row[2],
        "first_name": row[3],
        "last_name": row[4],
        "role": row[7],
        "department_id": row[8],
        "section_id": row[9],
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
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
