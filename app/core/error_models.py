# app/core/error_models.py
"""
Standardized error response models for consistent API error handling
"""

from datetime import datetime
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

class ErrorResponse(BaseModel):
    """Standard error response format"""
    error: str = Field(..., description="Human-readable error message")
    code: str = Field(..., description="Machine-readable error code")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional error details")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    request_id: Optional[str] = Field(None, description="Request identifier for tracking")

class ValidationError(ErrorResponse):
    """Validation-specific error response"""
    code: str = Field(default="VALIDATION_ERROR")
    field: Optional[str] = Field(None, description="Field that failed validation")
    value: Optional[Any] = Field(None, description="Invalid value that was provided")

class StateTransitionError(ErrorResponse):
    """State transition-specific error response"""
    code: str = Field(default="INVALID_TRANSITION")
    current_status: Optional[str] = Field(None, description="Current item status")
    requested_status: Optional[str] = Field(None, description="Requested status")
    allowed_transitions: Optional[list] = Field(None, description="List of allowed transitions")

class NotFoundError(ErrorResponse):
    """Resource not found error response"""
    code: str = Field(default="NOT_FOUND")
    resource_type: Optional[str] = Field(None, description="Type of resource that was not found")
    resource_id: Optional[str] = Field(None, description="ID of the resource that was not found")

class AuthenticationError(ErrorResponse):
    """Authentication-specific error response"""
    code: str = Field(default="AUTHENTICATION_ERROR")
    user_id: Optional[str] = Field(None, description="User ID if available")

class AuthorizationError(ErrorResponse):
    """Authorization-specific error response"""
    code: str = Field(default="AUTHORIZATION_ERROR")
    required_role: Optional[str] = Field(None, description="Required role for the operation")
    user_role: Optional[str] = Field(None, description="Current user role")

class ConcurrencyError(ErrorResponse):
    """Concurrency/conflict error response"""
    code: str = Field(default="CONCURRENCY_ERROR")
    conflict_type: Optional[str] = Field(None, description="Type of conflict")
    retry_after: Optional[int] = Field(None, description="Seconds to wait before retry")

class ServiceError(ErrorResponse):
    """General service error response"""
    code: str = Field(default="SERVICE_ERROR")
    service: Optional[str] = Field(None, description="Service that generated the error")
    operation: Optional[str] = Field(None, description="Operation that failed")

# Error code constants
class ErrorCodes:
    """Standardized error codes"""
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    NOT_FOUND = "NOT_FOUND"
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    AUTHORIZATION_ERROR = "AUTHORIZATION_ERROR"
    CONCURRENCY_ERROR = "CONCURRENCY_ERROR"
    SERVICE_ERROR = "SERVICE_ERROR"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    TIMEOUT_ERROR = "TIMEOUT_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    DATABASE_ERROR = "DATABASE_ERROR"
    FILE_SYSTEM_ERROR = "FILE_SYSTEM_ERROR"
    WEBSOCKET_ERROR = "WEBSOCKET_ERROR"
    CACHE_ERROR = "CACHE_ERROR"

# HTTP status code mapping
ERROR_STATUS_CODES = {
    ErrorCodes.VALIDATION_ERROR: 400,
    ErrorCodes.INVALID_TRANSITION: 400,
    ErrorCodes.AUTHENTICATION_ERROR: 401,
    ErrorCodes.AUTHORIZATION_ERROR: 403,
    ErrorCodes.NOT_FOUND: 404,
    ErrorCodes.CONCURRENCY_ERROR: 409,
    ErrorCodes.RATE_LIMIT_EXCEEDED: 429,
    ErrorCodes.TIMEOUT_ERROR: 408,
    ErrorCodes.SERVICE_ERROR: 500,
    ErrorCodes.DATABASE_ERROR: 500,
    ErrorCodes.FILE_SYSTEM_ERROR: 500,
    ErrorCodes.WEBSOCKET_ERROR: 500,
    ErrorCodes.CACHE_ERROR: 500,
    ErrorCodes.NETWORK_ERROR: 503,
}

def get_status_code_for_error(error_code: str) -> int:
    """Get appropriate HTTP status code for error code"""
    return ERROR_STATUS_CODES.get(error_code, 500)

def create_error_response(
    error_code: str,
    error_message: str,
    details: Optional[Dict[str, Any]] = None,
    request_id: Optional[str] = None
) -> ErrorResponse:
    """Create standardized error response"""
    return ErrorResponse(
        error=error_message,
        code=error_code,
        details=details,
        request_id=request_id
    )

def create_validation_error(
    field: str,
    value: Any,
    error_message: str,
    details: Optional[Dict[str, Any]] = None
) -> ValidationError:
    """Create validation error response"""
    return ValidationError(
        error=error_message,
        field=field,
        value=value,
        details=details
    )

def create_state_transition_error(
    current_status: str,
    requested_status: str,
    allowed_transitions: list,
    error_message: str,
    details: Optional[Dict[str, Any]] = None
) -> StateTransitionError:
    """Create state transition error response"""
    return StateTransitionError(
        error=error_message,
        current_status=current_status,
        requested_status=requested_status,
        allowed_transitions=allowed_transitions,
        details=details
    )

def create_not_found_error(
    resource_type: str,
    resource_id: str,
    error_message: str,
    details: Optional[Dict[str, Any]] = None
) -> NotFoundError:
    """Create not found error response"""
    return NotFoundError(
        error=error_message,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details
    )
