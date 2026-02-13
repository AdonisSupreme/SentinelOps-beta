from pydantic import BaseModel
from typing import Optional, Dict
from datetime import datetime

# --- Request payload for login ---
class SignInRequest(BaseModel):
    email: str
    password: str

# --- Unified user returned from backend ---
class UserResponse(BaseModel):
    id: str
    username: str
    email: str
    first_name: str
    last_name: str
    department: str = ""
    position: str = ""
    department_id: Optional[int] = None
    section_id: Optional[str] = None
    department_name: str = ""
    section_name: str = ""
    role: str
    central_id: str
    created_at: datetime
    raw_user: Optional[Dict] = {}

# --- Response from /auth/signin ---
class SignInResponse(BaseModel):
    token: str
    user: UserResponse
