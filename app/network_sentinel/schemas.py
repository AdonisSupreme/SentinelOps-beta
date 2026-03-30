from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


OverallStatus = Literal["UNKNOWN", "UP", "DEGRADED", "DOWN"]


class NetworkServiceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    address: str = Field(..., min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    enabled: bool = True
    check_icmp: bool = True
    check_tcp: bool = True
    timeout_ms: int = Field(default=3000, ge=250, le=60000)
    interval_seconds: int = Field(default=2, ge=1, le=3600)
    environment: str | None = Field(default=None, max_length=60)
    group_name: str | None = Field(default=None, max_length=120)
    owner_team: str | None = Field(default=None, max_length=120)
    tags: list[str] = Field(default_factory=list)
    color: str | None = Field(default=None, max_length=32)
    icon: str | None = Field(default=None, max_length=64)
    notes: str | None = None


class NetworkServiceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    address: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    enabled: bool | None = None
    check_icmp: bool | None = None
    check_tcp: bool | None = None
    timeout_ms: int | None = Field(default=None, ge=250, le=60000)
    interval_seconds: int | None = Field(default=None, ge=1, le=3600)
    environment: str | None = Field(default=None, max_length=60)
    group_name: str | None = Field(default=None, max_length=120)
    owner_team: str | None = Field(default=None, max_length=120)
    tags: list[str] | None = None
    color: str | None = Field(default=None, max_length=32)
    icon: str | None = Field(default=None, max_length=64)
    notes: str | None = None


class NetworkServiceListItem(BaseModel):
    id: UUID
    name: str
    address: str
    port: int | None
    enabled: bool
    check_icmp: bool
    check_tcp: bool
    timeout_ms: int
    interval_seconds: int
    environment: str | None
    group_name: str | None
    owner_team: str | None
    tags: list[str]
    color: str | None
    icon: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    status: dict | None = None


class ServiceCheckNowResponse(BaseModel):
    service_id: UUID
    checked_at: datetime
    overall_status: OverallStatus
    reason: str | None
    icmp: dict | None
    tcp: dict | None


class OutageItem(BaseModel):
    id: UUID
    service_id: UUID
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: int | None
    cause: str
    details: dict | None

