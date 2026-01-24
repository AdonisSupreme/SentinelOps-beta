from passlib.context import CryptContext
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from uuid import UUID
import jwt
from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("security")

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"
)

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


# =====================================================
# JWT Token Management
# =====================================================

def create_access_token(
    subject: str,
    session_id: UUID,
    role: str,
    expires_delta: Optional[timedelta] = None,
    auth_source: Optional[str] = None
) -> str:
    """
    Create a signed JWT access token.
    
    Args:
        subject: user_id
        session_id: auth_sessions.id
        role: user role
        expires_delta: custom expiry (default: ACCESS_TOKEN_EXPIRE_MINUTES)
    
    Returns:
        Signed JWT string
    """
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    # Use UTC time for JWT creation
    now_utc = datetime.now(timezone.utc)
    
    # Fetch session created_at from DB for consistency
    from app.db.database import get_connection
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT created_at FROM auth_sessions WHERE id = %s", (str(session_id),))
            row = cur.fetchone()
            issued_at = row[0] if row else now_utc
            
            # Ensure issued_at is in UTC
            if issued_at.tzinfo is None:
                issued_at = issued_at.replace(tzinfo=timezone.utc)

    # JWT expiry should match session TTL (24 hours) for consistency
    jwt_expiry = issued_at + timedelta(hours=24)
    
    payload = {
        "sub": str(subject),  # user_id
        "sid": str(session_id),  # session_id
        "role": role,
        "iat": int(now_utc.timestamp()),
        "exp": int(jwt_expiry.timestamp()),
    }
    if auth_source:
        payload["auth_source"] = auth_source
    print(f"Payload: {payload}")
    
    encoded_jwt = jwt.encode(
        payload,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM
    )
    print(f"Encoded JWT: {encoded_jwt}")
    
    log.warning(
        "TIME CHECK | now_utc=%s | issued_at=%s | jwt_expiry=%s",
        now_utc,
        issued_at,
        jwt_expiry
    )
    log.info(f"✅ Created JWT for user {subject}, expires at {jwt_expiry}")
    return encoded_jwt


def verify_and_decode_token(token: str) -> Dict[str, Any]:
    """
    Verify JWT signature and decode claims.
    
    Raises:
        jwt.ExpiredSignatureError if token expired
        jwt.InvalidSignatureError if signature invalid
        jwt.DecodeError if token malformed
    
    Returns:
        Decoded payload dict with keys: sub, sid, role, iat, exp
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        return payload
    except jwt.ExpiredSignatureError as e:
        log.warning(f"Token expired: {e}")
        raise
    except jwt.InvalidSignatureError as e:
        log.warning(f"Invalid token signature: {e}")
        raise
    except jwt.DecodeError as e:
        log.warning(f"Token decode error: {e}")
        raise
