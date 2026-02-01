# app/checklists/state_machine.py
"""
Centralized checklist state machine and transition policy.
Exposed via metadata endpoint for frontend consumption.
"""

from enum import Enum
from typing import Dict, List, Set, Optional

# -------------------------------------------------
# Enums matching database
# -------------------------------------------------
class ItemStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"

class ChecklistStatus(str, Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    PENDING_REVIEW = "PENDING_REVIEW"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_EXCEPTIONS = "COMPLETED_WITH_EXCEPTIONS"
    INCOMPLETE = "INCOMPLETE"  # Fixed: Match database schema

# -------------------------------------------------
# Transition rules (machine-readable)
# -------------------------------------------------
class TransitionRule:
    def __init__(
        self,
        from_status: str,
        to_status: str,
        allowed_roles: Optional[Set[str]] = None,
        requires_comment: bool = False,
        requires_reason: bool = False,
        description: Optional[str] = None
    ):
        self.from_status = from_status
        self.to_status = to_status
        self.allowed_roles = allowed_roles or set()
        self.requires_comment = requires_comment
        self.requires_reason = requires_reason
        self.description = description

# -------------------------------------------------
# Item-level transitions
# -------------------------------------------------
ITEM_TRANSITIONS: Dict[str, List[TransitionRule]] = {
    ItemStatus.PENDING.value: [
        TransitionRule(
            from_status=ItemStatus.PENDING.value,
            to_status=ItemStatus.IN_PROGRESS.value,
            description="Start working on the item"
        ),
        TransitionRule(
            from_status=ItemStatus.PENDING.value,
            to_status=ItemStatus.SKIPPED.value,
            requires_reason=True,
            description="Skip the item with a reason"
        ),
        TransitionRule(
            from_status=ItemStatus.PENDING.value,
            to_status=ItemStatus.FAILED.value,
            requires_reason=True,
            description="Mark the item as failed with a reason"
        ),
    ],
    ItemStatus.IN_PROGRESS.value: [
        TransitionRule(
            from_status=ItemStatus.IN_PROGRESS.value,
            to_status=ItemStatus.COMPLETED.value,
            description="Mark the item as completed"
        ),
        TransitionRule(
            from_status=ItemStatus.IN_PROGRESS.value,
            to_status=ItemStatus.SKIPPED.value,
            requires_reason=True,
            description="Skip the in-progress item with a reason"
        ),
        TransitionRule(
            from_status=ItemStatus.IN_PROGRESS.value,
            to_status=ItemStatus.FAILED.value,
            requires_reason=True,
            description="Mark the in-progress item as failed with a reason"
        ),
    ],
    ItemStatus.COMPLETED.value: [],
    ItemStatus.SKIPPED.value: [
        TransitionRule(
            from_status=ItemStatus.SKIPPED.value,
            to_status=ItemStatus.COMPLETED.value,
            description="Complete item after it was skipped"
        ),
    ],
    ItemStatus.FAILED.value: [
        TransitionRule(
            from_status=ItemStatus.FAILED.value,
            to_status=ItemStatus.COMPLETED.value,
            description="Complete item after issue was resolved"
        ),
    ],
}

# -------------------------------------------------
# Checklist-level transitions (supervisor only)
# -------------------------------------------------
CHECKLIST_TRANSITIONS: Dict[str, List[TransitionRule]] = {
    ChecklistStatus.OPEN.value: [
        TransitionRule(
            from_status=ChecklistStatus.OPEN.value,
            to_status=ChecklistStatus.IN_PROGRESS.value,
            allowed_roles={"SUPERVISOR", "MANAGER", "ADMIN"},
            description="Open checklist for work"
        ),
    ],
    ChecklistStatus.IN_PROGRESS.value: [
        TransitionRule(
            from_status=ChecklistStatus.IN_PROGRESS.value,
            to_status=ChecklistStatus.PENDING_REVIEW.value,
            allowed_roles={"SUPERVISOR", "MANAGER", "ADMIN"},
            description="Mark checklist as pending review"
        ),
    ],
    ChecklistStatus.PENDING_REVIEW.value: [
        TransitionRule(
            from_status=ChecklistStatus.PENDING_REVIEW.value,
            to_status=ChecklistStatus.COMPLETED.value,
            allowed_roles={"SUPERVISOR", "MANAGER", "ADMIN"},
            description="Approve and complete checklist"
        ),
        TransitionRule(
            from_status=ChecklistStatus.PENDING_REVIEW.value,
            to_status=ChecklistStatus.COMPLETED_WITH_EXCEPTIONS.value,
            allowed_roles={"SUPERVISOR", "MANAGER", "ADMIN"},
            description="Complete checklist with exceptions"
        ),
    ],
    ChecklistStatus.COMPLETED.value: [],
    ChecklistStatus.COMPLETED_WITH_EXCEPTIONS.value: [],
    ChecklistStatus.INCOMPLETE.value: [],
}

# -------------------------------------------------
# Public policy export
# -------------------------------------------------
def get_item_transition_policy() -> Dict:
    """Export item-level transition policy for frontend."""
    return {
        "type": "item",
        "states": [s.value for s in ItemStatus],
        "transitions": [
            {
                "from": tr.from_status,
                "to": tr.to_status,
                "allowed_roles": list(tr.allowed_roles) if tr.allowed_roles else None,
                "requires_comment": tr.requires_comment,
                "requires_reason": tr.requires_reason,
                "description": tr.description,
            }
            for rules in ITEM_TRANSITIONS.values()
            for tr in rules
        ],
    }

def get_checklist_transition_policy() -> Dict:
    """Export checklist-level transition policy for frontend."""
    return {
        "type": "checklist",
        "states": [s.value for s in ChecklistStatus],
        "transitions": [
            {
                "from": tr.from_status,
                "to": tr.to_status,
                "allowed_roles": list(tr.allowed_roles) if tr.allowed_roles else None,
                "requires_comment": tr.requires_comment,
                "requires_reason": tr.requires_reason,
                "description": tr.description,
            }
            for rules in CHECKLIST_TRANSITIONS.values()
            for tr in rules
        ],
    }

def is_item_transition_allowed(
    from_status: str,
    to_status: str,
    user_role: str
) -> bool:
    """Check if a user role can perform an item transition."""
    for rule in ITEM_TRANSITIONS.get(from_status, []):
        if rule.to_status == to_status:
            return not rule.allowed_roles or user_role in rule.allowed_roles
    return False

def is_checklist_transition_allowed(
    from_status: str,
    to_status: str,
    user_role: str
) -> bool:
    """Check if a user role can perform a checklist transition."""
    for rule in CHECKLIST_TRANSITIONS.get(from_status, []):
        if rule.to_status == to_status:
            return not rule.allowed_roles or user_role in rule.allowed_roles
    return False
