# app/checklists/user_service.py
"""
User service for identifying and managing user information
"""

from typing import Dict, Optional, Any
from uuid import UUID
from datetime import datetime

class UserService:
    """Service for managing user information and identification"""
    
    # User database - in a real system this would come from a database
    USERS_DB = {
        'ashumba': {
            'id': 'ashumba-user-id',
            'username': 'ashumba',
            'email': 'ashumba@sentinel.ops',
            'first_name': 'Ashumba',
            'last_name': 'Operator',
            'role': 'senior_operator',
            'display_name': 'Ashumba'
        },
        'ashumba_alt': {
            'id': '785cfda9-38c7-4b8d-844a-5c8c7672a12b',  # The actual user ID being used
            'username': 'ashumba',
            'email': 'ashumba@sentinel.ops',
            'first_name': 'Ashumba',
            'last_name': 'Operator',
            'role': 'senior_operator',
            'display_name': 'Ashumba'
        },
        'system': {
            'id': 'system-user-id',
            'username': 'system',
            'email': 'system@sentinel.ops',
            'first_name': 'System',
            'last_name': 'User',
            'role': 'system',
            'display_name': 'System'
        }
    }
    
    @staticmethod
    def get_user_by_id(user_id: UUID) -> Optional[Dict[str, Any]]:
        """Get user information by user ID"""
        user_id_str = str(user_id)
        
        # Check if user_id matches any known user
        for user in UserService.USERS_DB.values():
            if user['id'] == user_id_str:
                return user.copy()
        
        # Check if user_id contains 'ashumba' (for backward compatibility)
        if 'ashumba' in user_id_str.lower():
            return UserService.USERS_DB['ashumba'].copy()
        
        # Return system user as fallback
        return UserService.USERS_DB['system'].copy()
    
    @staticmethod
    def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
        """Get user information by username"""
        return UserService.USERS_DB.get(username.lower())
    
    @staticmethod
    def identify_user(user_id: Optional[UUID] = None, username: Optional[str] = None) -> Dict[str, Any]:
        """Identify user from various inputs"""
        if user_id:
            user = UserService.get_user_by_id(user_id)
            if user:
                return user
        
        if username:
            user = UserService.get_user_by_username(username)
            if user:
                return user
        
        # Default to ashumba if no user specified (as requested)
        return UserService.USERS_DB['ashumba'].copy()
    
    @staticmethod
    def create_user_info(user_id: Optional[UUID] = None, username: Optional[str] = None) -> Dict[str, Any]:
        """Create user info object for API responses"""
        user = UserService.identify_user(user_id, username)
        
        return {
            'id': user['id'],
            'username': user['username'],
            'email': user['email'],
            'first_name': user['first_name'],
            'last_name': user['last_name'],
            'role': user['role'],
            'display_name': user.get('display_name', user['username'])
        }
    
    @staticmethod
    def is_ashumba(user_id: Optional[UUID] = None, username: Optional[str] = None) -> bool:
        """Check if the user is ashumba"""
        user = UserService.identify_user(user_id, username)
        return user['username'] == 'ashumba'
    
    @staticmethod
    def get_ashumba_user() -> Dict[str, Any]:
        """Get ashumba user info"""
        return UserService.USERS_DB['ashumba'].copy()
