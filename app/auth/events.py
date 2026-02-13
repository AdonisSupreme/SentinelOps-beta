# app/auth/events.py
"""
Authentication Event Logging Service
Logs all significant auth events to database
- Login success/failure
- Logout
- Session creation/revocation
- Failed attempts
"""

from typing import Optional
from uuid import UUID
from datetime import datetime, timezone
import json

from app.db.database import get_connection
from app.core.logging import get_logger

log = get_logger("auth-events")


def _safe_load_json(val):
    """Return a dict from a JSON string/bytes or return dict as-is.

    Protects against drivers already returning parsed dicts.
    """
    if not val:
        return {}
    if isinstance(val, dict):
        return val
    if isinstance(val, (bytes, bytearray)):
        try:
            return json.loads(val.decode("utf-8"))
        except Exception:
            return {}
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return {}
    return {}


class AuthEventLogger:
    """Logs authentication events to database"""
    
    @staticmethod
    def log_login_success(
        user_id: UUID,
        username: str,
        email: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> dict:
        """Log successful login"""
        try:
            metadata = {
                "username": username,
                "email": email,
                "source": "sentinel",
                "login_type": "password"
            }
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO auth_events (
                            user_id, event_type, event_time, ip_address, user_agent, metadata
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s
                        ) RETURNING id, user_id, event_type, event_time, ip_address, user_agent, metadata
                    """, (
                        user_id, 'LOGIN_SUCCESS', datetime.now(timezone.utc),
                        ip_address, user_agent, json.dumps(metadata)
                    ))
                    
                    result = cur.fetchone()
                    conn.commit()
                    
                    log.info(f"✅ Login success: {username} ({user_id}) from {ip_address}")
                    
                    return {
                        'id': str(result[0]),
                        'user_id': str(result[1]) if result[1] else None,
                        'event_type': result[2],
                        'event_time': result[3].isoformat() if result[3] else None,
                        'ip_address': result[4],
                        'user_agent': result[5],
                        'metadata': _safe_load_json(result[6])
                    }
        except Exception as e:
            log.error(f"Failed to log login success: {e}")
            raise
    
    @staticmethod
    def log_login_failure(
        email: str,
        reason: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> dict:
        """Log failed login attempt (no user_id since auth failed)"""
        try:
            metadata = {
                "email": email,
                "reason": reason,
                "source": "sentinel"
            }
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO auth_events (
                            user_id, event_type, event_time, ip_address, user_agent, metadata
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s
                        ) RETURNING id, event_type, event_time, ip_address, user_agent, metadata
                    """, (
                        None, 'LOGIN_FAILURE', datetime.now(timezone.utc),
                        ip_address, user_agent, json.dumps(metadata)
                    ))
                    
                    result = cur.fetchone()
                    conn.commit()
                    
                    log.warning(f"❌ Login failure: {email} - {reason} from {ip_address}")
                    
                    return {
                        'id': str(result[0]),
                        'event_type': result[1],
                        'event_time': result[2].isoformat() if result[2] else None,
                        'ip_address': result[3],
                        'user_agent': result[4],
                        'metadata': _safe_load_json(result[5])
                    }
        except Exception as e:
            log.error(f"Failed to log login failure: {e}")
            raise
    
    @staticmethod
    def log_logout(
        user_id: UUID,
        username: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> dict:
        """Log user logout"""
        try:
            metadata = {
                "username": username
            }
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO auth_events (
                            user_id, event_type, event_time, ip_address, user_agent, metadata
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s
                        ) RETURNING id, user_id, event_type, event_time
                    """, (
                        user_id, 'LOGOUT', datetime.now(timezone.utc),
                        ip_address, user_agent, json.dumps(metadata)
                    ))
                    
                    result = cur.fetchone()
                    conn.commit()
                    
                    log.info(f"🚪 Logout: {username} ({user_id})")
                    
                    return {
                        'id': str(result[0]),
                        'user_id': str(result[1]) if result[1] else None,
                        'event_type': result[2],
                        'event_time': result[3].isoformat() if result[3] else None
                    }
        except Exception as e:
            log.error(f"Failed to log logout: {e}")
            raise
    
    @staticmethod
    def log_session_created(
        user_id: UUID,
        username: str,
        session_expires_at,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> dict:
        """Log session creation"""
        try:
            metadata = {
                "username": username,
                "session_duration_hours": 24
            }
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO auth_events (
                            user_id, event_type, event_time, ip_address, user_agent, metadata
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s
                        ) RETURNING id, user_id, event_type, event_time
                    """, (
                        user_id, 'SESSION_CREATED', datetime.now(timezone.utc),
                        ip_address, user_agent, json.dumps(metadata)
                    ))
                    
                    result = cur.fetchone()
                    conn.commit()
                    
                    log.info(f"🔑 Session created: {username} ({user_id})")
                    
                    return {
                        'id': str(result[0]),
                        'user_id': str(result[1]) if result[1] else None,
                        'event_type': result[2],
                        'event_time': result[3].isoformat() if result[3] else None
                    }
        except Exception as e:
            log.error(f"Failed to log session creation: {e}")
            raise
    
    @staticmethod
    def log_session_revoked(
        user_id: UUID,
        username: str,
        reason: str = "User logout",
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> dict:
        """Log session revocation"""
        try:
            metadata = {
                "username": username,
                "reason": reason
            }
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO auth_events (
                            user_id, event_type, event_time, ip_address, user_agent, metadata
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s
                        ) RETURNING id, user_id, event_type, event_time
                    """, (
                        user_id, 'SESSION_REVOKED', datetime.now(timezone.utc),
                        ip_address, user_agent, json.dumps(metadata)
                    ))
                    
                    result = cur.fetchone()
                    conn.commit()
                    
                    log.info(f"🔓 Session revoked: {username} ({user_id}) - {reason}")
                    
                    return {
                        'id': str(result[0]),
                        'user_id': str(result[1]) if result[1] else None,
                        'event_type': result[2],
                        'event_time': result[3].isoformat() if result[3] else None
                    }
        except Exception as e:
            log.error(f"Failed to log session revocation: {e}")
            raise
    
    @staticmethod
    def log_invalid_token(
        reason: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> dict:
        """Log invalid token attempt"""
        try:
            metadata = {
                "reason": reason
            }
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO auth_events (
                            user_id, event_type, event_time, ip_address, user_agent, metadata
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s
                        ) RETURNING id, event_type, event_time
                    """, (
                        None, 'INVALID_TOKEN', datetime.now(timezone.utc),
                        ip_address, user_agent, json.dumps(metadata)
                    ))
                    
                    result = cur.fetchone()
                    conn.commit()
                    
                    log.warning(f"🔐 Invalid token attempt: {reason}")
                    
                    return {
                        'id': str(result[0]),
                        'event_type': result[1],
                        'event_time': result[2].isoformat() if result[2] else None
                    }
        except Exception as e:
            log.error(f"Failed to log invalid token: {e}")
            raise
