# app/ops/__init__.py
"""Operational events and audit logging"""

from app.ops.events import OpsEventLogger

__all__ = ['OpsEventLogger']
