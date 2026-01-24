from fastapi import APIRouter, HTTPException, Depends, Header, Request
from fastapi.responses import JSONResponse

from app.auth.service import authenticate_user, AuthenticationError, get_user_from_token, check_ad_status
from app.auth.schemas import SignInRequest, UserResponse
from app.core.logging import get_logger
from app.core.security import create_access_token
from app.db.database import get_connection
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import json

log = get_logger("auth-router")

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Test endpoint to verify router is working
@router.get("/test")
def test_endpoint():
    """Simple test endpoint"""
    return {"message": "Router is working!", "timestamp": str(datetime.now())}

# --- Sign in ---
@router.post("/signin")
def sign_in(payload: SignInRequest, request: Request):
    """
    Authenticate user via Sentinel database, issue JWT.
    """
    print("=" * 50)
    print("BACKEND: SIGNIN ENDPOINT CALLED!")
    print("=" * 50)
    
    print(f" [BACKEND] Signin request received")
    print(f" [BACKEND] Email: {payload.email}")
    print(f" [BACKEND] Password length: {len(payload.password)}")
    print(f" [BACKEND] Client IP: {request.client.host if request.client else 'unknown'}")
    print(f" [BACKEND] User-Agent: {request.headers.get('user-agent', 'unknown')[:100]}...")
    
    try:
        print(f" [BACKEND] Calling authenticate_user...")
        user, auth_source = authenticate_user(payload.email, payload.password, request)
        user_id = user["id"]
        
        print(f" [BACKEND] Authentication successful!")
        print(f" [BACKEND] User: {user['username']} ({user['email']})")
        print(f" [BACKEND] Role: {user['role']}")
        print(f" [BACKEND] Auth source: {auth_source}")
        
        # Issue JWT bound to session
        print(" [BACKEND] Issuing JWT bound to session")
        token = create_access_token(
            subject=user_id,
            session_id=user["session_id"],  # Use the actual session_id from authentication
            role=user["role"],
            auth_source=auth_source
        )
        
        response_data = {
            "token": token,
            "user": {
                "id": user["id"],
                "username": user["username"],
                "email": user["email"],
                "first_name": user["first_name"],
                "last_name": user["last_name"],
                "department": user.get("department", ""),
                "position": user.get("position", ""),
                "role": user["role"]
            }
        }
        
        print(f" [BACKEND] Sending response with token and user data")
        print(f" [BACKEND] Token length: {len(token)}")
        print(f" [BACKEND] User fields: {list(response_data['user'].keys())}")
        
        return response_data

    except AuthenticationError as exc:
        log.warning(f" [BACKEND] Authentication failed: {exc.message}")
        print(f"Login failed for {payload.email}: {exc.message}")
        
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.code,
                "message": exc.message,
                "context": exc.context,
            },
        )


# --- AD Status ---
@router.get("/ad/status")
def ad_status():
    """
    Returns Active Directory availability status.
    """
    return check_ad_status()


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
        # Ensure response matches frontend MeResponse interface
        return {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "first_name": user["first_name"],
            "last_name": user["last_name"],
            "department": user.get("department", ""),
            "position": user.get("position", ""),
            "role": user["role"],
            "central_id": user.get("central_id", ""),
            "created_at": user.get("created_at"),
            "raw_user": user.get("raw_user", {})
        }
    except AuthenticationError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.code,
                "message": exc.message,
                "context": exc.context,
            },
        )


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

    # Revoke session in database
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE auth_sessions SET revoked_at = NOW() WHERE id = %s AND user_id = %s",
                    (session_id, user_id)
                )
                conn.commit()
        print(f"Logout called for user {user_id}, session {session_id}")
    except Exception as e:
        log.error(f"Failed to revoke session: {e}")
    
    return {"detail": "Logged out successfully"}