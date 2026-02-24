# app/auth/dependencies.py
"""
Authentication dependencies for FastAPI endpoints and WebSocket connections
"""

from fastapi import HTTPException, Header, WebSocket, Query
from .service import get_user_from_token, AuthenticationError
from app.core.logging import get_logger

log = get_logger("auth-dependencies")

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

async def get_current_user_websocket(token: str) -> dict:
    """WebSocket dependency that returns the current user based on token query parameter."""
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")
    
    try:
        user = get_user_from_token(token)
        return user
    except AuthenticationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
