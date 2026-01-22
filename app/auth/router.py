from fastapi import APIRouter, HTTPException, Depends, Header, Request
from app.auth.service import authenticate_user, AuthenticationError, get_user_from_token
from app.auth.schemas import SignInRequest, UserResponse
from app.core.logging import get_logger
from app.core.security import create_access_token
from app.db.database import get_connection
from datetime import datetime, timedelta
from uuid import uuid4
import json

log = get_logger("auth-router")

router = APIRouter(prefix="/auth", tags=["Authentication"])

# --- Sign in ---
@router.post("/signin")
def sign_in(payload: SignInRequest, request: Request):
    """
    Authenticate user via central auth, authorize locally, create session, issue JWT.
    """
    try:
        user = authenticate_user(payload.email, payload.password, request)
        user_id = user["id"]
        
        # Create auth session in DB
        with get_connection() as conn:
            with conn.cursor() as cur:
                session_id = str(uuid4())
                ip_address = request.client.host if request.client else "unknown"
                user_agent = request.headers.get("user-agent", "unknown")
                
                # Enforce UTC time authority
                created_at = datetime.utcnow()
                
                # Session expires in ACCESS_TOKEN_EXPIRE_MINUTES
                from app.core.config import settings
                expires_at = created_at + timedelta(
                    minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
                )
                
                cur.execute(
                    """
                    INSERT INTO auth_sessions 
                    (id, user_id, ip_address, user_agent, expires_at, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (session_id, user_id, ip_address, user_agent, expires_at, datetime.utcnow())
                )
                conn.commit()
                
                # Log LOGIN_SUCCESS event
                cur.execute(
                    """
                    INSERT INTO auth_events 
                    (user_id, event_type, ip_address, user_agent, metadata)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        user_id, 
                        "LOGIN_SUCCESS", 
                        ip_address, 
                        user_agent,
                        json.dumps({"email": payload.email})
                    )
                )
                conn.commit()
        
        # Issue JWT bound to session
        token = create_access_token(
            subject=user_id,
            session_id=session_id,
            role=user["role"]
        )
        
        return {
            "token": token,
            "user": user
        }

    except AuthenticationError as exc:
        log.warning(f"🚫 Authentication failed: {exc}")
        # Log LOGIN_FAILURE
        with get_connection() as conn:
            with conn.cursor() as cur:
                ip_address = request.client.host if request.client else "unknown"
                user_agent = request.headers.get("user-agent", "unknown")
                cur.execute(
                    """
                    INSERT INTO auth_events 
                    (event_type, ip_address, user_agent, metadata)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        "LOGIN_FAILURE",
                        ip_address,
                        user_agent,
                        json.dumps({"email": payload.email, "reason": str(exc)})
                    )
                )
                conn.commit()
        
        raise HTTPException(status_code=401, detail=str(exc))


# --- Current user endpoint ---
@router.get("/me", response_model=UserResponse)
def me(authorization: str = Header(None)):
    """
    Returns the current user based on the Bearer token.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")

    token = authorization.split(" ")[1]

    try:
        user = get_user_from_token(token)
        return user
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc))


# --- Logout endpoint ---
@router.post("/logout")
def logout(authorization: str = Header(None)):
    """
    Revoke session and invalidate token.
    Logout is idempotent: accepts valid or expired tokens.
    Never returns 401 for normal logout flow.
    """
    if not authorization or not authorization.startswith("Bearer "):
        # No token provided: nothing to revoke; return success
        log.info("Logout called without token; no session to revoke")
        return {"detail": "Logged out successfully"}

    token = authorization.split(" ")[1]

    try:
        from app.core.security import verify_and_decode_token
        payload = verify_and_decode_token(token)
    except Exception as e:
        # Token invalid/expired: still return success so frontend can clean up
        log.info(f"Logout called with invalid/expired token: {e}")
        return {"detail": "Logged out successfully"}

    user_id = payload.get("sub")
    session_id = payload.get("sid")

    if not user_id or not session_id:
        log.warning("Logout token missing required claims")
        return {"detail": "Logged out successfully"}

    # Revoke session if present
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE auth_sessions
                SET revoked_at = %s
                WHERE id = %s AND user_id = %s AND revoked_at IS NULL
                """,
                (datetime.utcnow(), session_id, user_id)
            )
            conn.commit()
            
            # Log LOGOUT event with UTC authority
            cur.execute(
                """
                INSERT INTO auth_events 
                (user_id, event_type, ip_address, user_agent, metadata)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    user_id,
                    "LOGOUT",
                    request.client.host if request.client else "unknown",
                    request.headers.get("user-agent", "unknown"),
                    json.dumps({"session_id": session_id})
                )
            )
            conn.commit()

    return {"detail": "Logged out successfully"}