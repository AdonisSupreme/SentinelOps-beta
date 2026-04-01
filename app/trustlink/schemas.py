"""Pydantic schemas for Trustlink API payloads."""

from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


RunType = Literal["manual", "scheduled"]
RunStatus = Literal["pending", "running", "success", "failed", "duplicate"]
StartStatus = Literal["scheduled", "exists", "failed"]
StepStatus = Literal["pending", "running", "completed", "failed"]


class TrustlinkRunRequest(BaseModel):
    run_type: RunType = "manual"
    force: bool = False


class TrustlinkRunStartResponse(BaseModel):
    status: StartStatus
    run_id: Optional[UUID] = None
    run_type: Optional[RunType] = None
    triggered_by: Optional[str] = None
    triggered_by_display: Optional[str] = None
    file_path: Optional[str] = None
    file_name: Optional[str] = None
    options: Optional[List[Literal["download", "overwrite"]]] = None
    detail: Optional[str] = None


class TrustlinkStep(BaseModel):
    id: int
    run_id: UUID
    step_name: str
    status: StepStatus
    row_count: int = 0
    duration_ms: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class TrustlinkRunListItem(BaseModel):
    id: UUID
    run_date: date
    run_type: RunType
    triggered_by: Optional[str] = None
    triggered_by_display: Optional[str] = None
    status: RunStatus
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    total_rows: int = 0
    total_duration_ms: int = 0
    file_path: Optional[str] = None
    file_name: Optional[str] = None
    file_status: Literal["available", "deleted", "not_generated"] = "not_generated"
    file_present: bool = False
    error_message: Optional[str] = None


class TrustlinkRunDetail(BaseModel):
    id: UUID
    run_date: date
    run_type: RunType
    triggered_by: Optional[str] = None
    triggered_by_display: Optional[str] = None
    status: RunStatus
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    file_path: Optional[str] = None
    file_name: Optional[str] = None
    file_status: Literal["available", "deleted", "not_generated"] = "not_generated"
    file_present: bool = False
    file_hash: Optional[str] = None
    integrity_report_path: Optional[str] = None
    total_rows: int = 0
    idc_rows: int = 0
    digipay_rows: int = 0
    extract_duration_ms: int = 0
    transform_duration_ms: int = 0
    validation_duration_ms: int = 0
    total_duration_ms: int = 0
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None


class TrustlinkTodayStatusResponse(BaseModel):
    status: Literal["none", "exists", "running"]
    run: Optional[TrustlinkRunDetail] = None
    has_file: bool = False
    options: List[Literal["download", "overwrite"]] = Field(default_factory=list)


class TrustlinkFileDeleteResponse(BaseModel):
    deleted: bool
    run_id: UUID
    file_status: Literal["available", "deleted", "not_generated"]
    detail: str
