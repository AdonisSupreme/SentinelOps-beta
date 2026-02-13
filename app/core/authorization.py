# app/core/authorization.py
"""
Capability-based authorization model.
Roles map to capabilities; services check capabilities, not raw roles.
"""

from typing import Set, Dict

# -------------------------------------------------
# Capability definitions
# -------------------------------------------------
class Capabilities:
    # Checklist capabilities
    PARTICIPANT_JOIN_CHECKLIST = "PARTICIPANT_JOIN_CHECKLIST"
    PARTICIPANT_UPDATE_ITEM = "PARTICIPANT_UPDATE_ITEM"
    SUPERVISOR_COMPLETE_CHECKLIST = "SUPERVISOR_COMPLETE_CHECKLIST"
    SUPERVISOR_REVIEW_CHECKLIST = "SUPERVISOR_REVIEW_CHECKLIST"

    # Gamification capabilities
    VIEW_LEADERBOARD = "VIEW_LEADERBOARD"
    VIEW_OWN_SCORES = "VIEW_OWN_SCORES"

    # Notifications capabilities
    MANAGE_OWN_NOTIFICATIONS = "MANAGE_OWN_NOTIFICATIONS"

# -------------------------------------------------
# Role → Capability mapping
# -------------------------------------------------
ROLE_CAPABILITIES: Dict[str, Set[str]] = {
    "USER": {
        Capabilities.PARTICIPANT_JOIN_CHECKLIST,
        Capabilities.PARTICIPANT_UPDATE_ITEM,
        Capabilities.VIEW_OWN_SCORES,
        Capabilities.MANAGE_OWN_NOTIFICATIONS,
    },
    "MANAGER": {
        Capabilities.PARTICIPANT_JOIN_CHECKLIST,
        Capabilities.PARTICIPANT_UPDATE_ITEM,
        Capabilities.SUPERVISOR_COMPLETE_CHECKLIST,
        Capabilities.SUPERVISOR_REVIEW_CHECKLIST,
        Capabilities.VIEW_LEADERBOARD,
        Capabilities.VIEW_OWN_SCORES,
        Capabilities.MANAGE_OWN_NOTIFICATIONS,
    },
    "ADMIN": {
        Capabilities.SUPERVISOR_COMPLETE_CHECKLIST,
        Capabilities.SUPERVISOR_REVIEW_CHECKLIST,
        Capabilities.VIEW_LEADERBOARD,
        Capabilities.VIEW_OWN_SCORES,
        Capabilities.MANAGE_OWN_NOTIFICATIONS,
    },
}

def is_admin(user: dict) -> bool:
    """Case-insensitive admin check for user dict."""
    return (user.get("role") or "").lower() == "admin"


def is_manager_or_admin(user: dict) -> bool:
    """Check if user is manager or admin."""
    r = (user.get("role") or "").lower()
    return r in ("admin", "manager")


def has_capability(role: str, capability: str) -> bool:
    """Check if a role grants a capability. Role is normalized (admin/manager/user)."""
    r = (role or "").upper()
    # Map DB role names to capability keys
    role_map = {"ADMIN": "ADMIN", "MANAGER": "MANAGER", "USER": "USER"}
    key = role_map.get(r, r)
    return capability in ROLE_CAPABILITIES.get(key, set())

def get_capabilities_for_role(role: str) -> Set[str]:
    """Return all capabilities granted to a role."""
    return ROLE_CAPABILITIES.get(role, set())

def get_authorization_policy() -> Dict:
    """Export the full capability policy for frontend consumption."""
    return {
        "roles": {
            role: list(caps)
            for role, caps in ROLE_CAPABILITIES.items()
        },
        "capabilities": {
            "definitions": {
                cap: {
                    "description": _describe_capability(cap),
                    "category": _category_for_capability(cap),
                }
                for cap in {
                    c for caps in ROLE_CAPABILITIES.values() for c in caps
                }
            },
        },
    }

def _describe_capability(cap: str) -> str:
    """Human-readable description for a capability."""
    descriptions = {
        Capabilities.PARTICIPANT_JOIN_CHECKLIST: "Join checklist instances as a participant",
        Capabilities.PARTICIPANT_UPDATE_ITEM: "Update status of checklist items",
        Capabilities.SUPERVISOR_COMPLETE_CHECKLIST: "Mark checklists as completed (supervisor action)",
        Capabilities.SUPERVISOR_REVIEW_CHECKLIST: "Review and approve checklists",
        Capabilities.VIEW_LEADERBOARD: "View gamification leaderboard",
        Capabilities.VIEW_OWN_SCORES: "View own gamification scores",
        Capabilities.MANAGE_OWN_NOTIFICATIONS: "Manage own notifications (mark read, preferences)",
    }
    return descriptions.get(cap, cap)

def _category_for_capability(cap: str) -> str:
    """Category grouping for UI."""
    mapping = {
        Capabilities.PARTICIPANT_JOIN_CHECKLIST: "checklists",
        Capabilities.PARTICIPANT_UPDATE_ITEM: "checklists",
        Capabilities.SUPERVISOR_COMPLETE_CHECKLIST: "checklists",
        Capabilities.SUPERVISOR_REVIEW_CHECKLIST: "checklists",
        Capabilities.VIEW_LEADERBOARD: "gamification",
        Capabilities.VIEW_OWN_SCORES: "gamification",
        Capabilities.MANAGE_OWN_NOTIFICATIONS: "notifications",
    }
    return mapping.get(cap, "other")
