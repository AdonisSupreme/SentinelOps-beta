from fastapi import APIRouter, HTTPException, Depends, Header
from app.auth.service import authenticate_user, AuthenticationError, get_user_from_token
from app.auth.schemas import SignInRequest, UserResponse
from app.core.logging import get_logger

log = get_logger("auth-router")

router = APIRouter(prefix="/auth", tags=["Authentication"])

# --- Sign in ---
@router.post("/signin")
def sign_in(payload: SignInRequest):
    try:
        user = authenticate_user(payload.email, payload.password)

        # For now, use a dummy JWT (replace with real JWT later)
        token = f"dummy-jwt-for-{user['username']}"

        return {
            "token": token,
            "user": user
        }

    except AuthenticationError as exc:
        log.warning(f"🚫 Authentication failed: {exc}")
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