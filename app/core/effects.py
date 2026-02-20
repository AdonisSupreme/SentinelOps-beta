# app/core/effects.py
"""
Side-effect disclosure utilities.
Endpoints can declare what they trigger (notifications, gamification, events).
"""

from enum import Enum
from typing import List, Dict, Any

class EffectType(str, Enum):
    NOTIFICATION_CREATED = "NOTIFICATION_CREATED"
    WEBSOCKET_BROADCAST = "WEBSOCKET_BROADCAST"
    POINTS_AWARDED = "POINTS_AWARDED"
    AUDIT_EVENT = "AUDIT_EVENT"
    BACKGROUND_TASK = "BACKGROUND_TASK"
    CHECKLIST_JOINED = "CHECKLIST_JOINED"
    CHECKLIST_COMPLETED = "CHECKLIST_COMPLETED"
    ITEM_UPDATED = "ITEM_UPDATED"

class EffectDisclosure:
    def __init__(self, effects: List[EffectType], metadata: Dict[str, Any] = None):
        self.effects = effects
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "effects": [e.value for e in self.effects],
            "metadata": self.metadata,
        }

def disclose_effects(*effects: EffectType, **metadata) -> EffectDisclosure:
    """Helper to create an EffectDisclosure."""
    return EffectDisclosure(list(effects), metadata)
