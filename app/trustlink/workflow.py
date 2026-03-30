"""Workflow orchestration for Trustlink extraction flows.

Implements the extraction pipeline and persists run/step audit records
via `app.trustlink.db_service.TrustlinkDBService`.
"""

from datetime import datetime, timezone, date
from pathlib import Path
import hashlib
from typing import Optional, Dict, Any
import pandas as pd

from app.core.logging import get_logger
from app.trustlink import db_service as dbs
from app.trustlink import extractor, cleaning
from uuid import UUID as UUIDType

# Notifications + email
from app.notifications.db_service import NotificationDBService
from app.core.emailer import send_email_fire_and_forget, SMTP_FROM
import os

log = get_logger("trustlink-workflow")


def _now_utc():
    return datetime.now(timezone.utc)


def _is_retryable_oracle_error(exc: Exception) -> bool:
    message = str(exc).lower()
    retryable_markers = (
        "dpy-6005",
        "ora-12170",
        "ora-12541",
        "ora-12545",
        "ora-12514",
        "ora-12537",
        "timed out",
        "timeout",
        "cannot connect",
        "connection refused",
        "connection reset",
        "network is unreachable",
    )
    return any(marker in message for marker in retryable_markers)


def _allow_idc_timeout_fallback() -> bool:
    return os.getenv("TRUSTLINK_ENABLE_IDC_TIMEOUT_FALLBACK", "true").strip().lower() in {"1", "true", "yes", "on"}


def run_extraction(run_type: str = "manual", triggered_by: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    """Run the full Trustlink extraction workflow.

    Returns a dict summarising result and run identifiers. All DB operations
    use `TrustlinkDBService` (no direct SQL here).
    """
    # 1. check existing run for today
    today = date.today()
    existing = dbs.TrustlinkDBService.get_run_by_date(today)
    if existing and not force:
        return {"status": "ALREADY_EXISTS", "run_id": existing.get("id"), "file_path": existing.get("file_path")}

    # 2. create run record (status=running)
    run_id = None
    active_step_name: Optional[str] = None
    active_step_id: Optional[int] = None
    run_result = dbs.TrustlinkDBService.create_run({
        "run_date": today,
        "run_type": run_type,
        "triggered_by": triggered_by,
        "status": "running",
        "started_at": _now_utc(),
        "force": force,
    })
    run = run_result.get("run") if isinstance(run_result, dict) else run_result

    if not run:
        raise RuntimeError("Unable to initialize Trustlink run")

    if isinstance(run_result, dict) and not run_result.get("created") and not force:
        return {"status": "ALREADY_EXISTS", "run_id": run.get("id"), "file_path": run.get("file_path")}

    run_id = run.get("id")

    steps: Dict[str, Dict[str, Any]] = {}
    extract_duration_ms = 0
    transform_duration_ms = 0
    validation_duration_ms = 0
    run_warnings: list[str] = []

    try:
        # -------------------- IDC_EXTRACTION --------------------
        step = dbs.TrustlinkDBService.create_step(run_id, "IDC_EXTRACTION")
        steps["IDC_EXTRACTION"] = step
        active_step_name = "IDC_EXTRACTION"
        active_step_id = step["id"]
        s_start = _now_utc()
        dbs.TrustlinkDBService.update_step(
            step["id"],
            {
                "status": "running",
                "started_at": s_start,
                "completed_at": None,
                "duration_ms": 0,
                "row_count": 0,
                "metadata": {"phase": "started"},
            },
        )

        idc_fallback = False
        idc_fallback_reason: Optional[str] = None
        try:
            idc_df = extractor.extract_idc_accounts()
        except Exception as exc:
            if _allow_idc_timeout_fallback() and _is_retryable_oracle_error(exc):
                idc_fallback = True
                idc_fallback_reason = f"IDC source temporarily unreachable ({exc})"
                run_warnings.append(idc_fallback_reason)
                log.warning("IDC extraction fallback activated: using empty IDC dataset for this run")
                idc_df = pd.DataFrame()
            else:
                raise RuntimeError(f"IDC source extraction failed: {exc}") from exc
        idc_count = int(len(idc_df)) if idc_df is not None else 0

        s_end = _now_utc()
        s_dur = int((s_end - s_start).total_seconds() * 1000)
        extract_duration_ms += s_dur
        idc_metadata: Dict[str, Any] = {"source": "oracle", "rows": idc_count}
        if idc_fallback:
            idc_metadata["fallback"] = "empty_idc_dataset"
            idc_metadata["warning"] = idc_fallback_reason

        steps["IDC_EXTRACTION"] = dbs.TrustlinkDBService.update_step(
            step["id"],
            {
                "status": "completed",
                "row_count": idc_count,
                "duration_ms": s_dur,
                "completed_at": s_end,
                "metadata": idc_metadata,
            },
        ) or step

        # -------------------- DIGIPAY_EXTRACTION --------------------
        step = dbs.TrustlinkDBService.create_step(run_id, "DIGIPAY_EXTRACTION")
        steps["DIGIPAY_EXTRACTION"] = step
        active_step_name = "DIGIPAY_EXTRACTION"
        active_step_id = step["id"]
        s_start = _now_utc()
        dbs.TrustlinkDBService.update_step(
            step["id"],
            {
                "status": "running",
                "started_at": s_start,
                "completed_at": None,
                "duration_ms": 0,
                "row_count": 0,
                "metadata": {"phase": "started"},
            },
        )

        try:
            digipay_df = extractor.extract_digipay_accounts()
        except Exception as exc:
            raise RuntimeError(f"DIGIPAY source extraction failed: {exc}") from exc

        try:
            # split into USD and ZWG
            usd_df, zwg_df = extractor.split_digipay(digipay_df)
        except Exception as exc:
            raise RuntimeError(f"DIGIPAY source split failed: {exc}") from exc
        digipay_count = int(len(digipay_df)) if digipay_df is not None else 0

        s_end = _now_utc()
        s_dur = int((s_end - s_start).total_seconds() * 1000)
        extract_duration_ms += s_dur
        steps["DIGIPAY_EXTRACTION"] = dbs.TrustlinkDBService.update_step(
            step["id"],
            {
                "status": "completed",
                "row_count": digipay_count,
                "duration_ms": s_dur,
                "completed_at": s_end,
                "metadata": {"source": "postgres", "rows": digipay_count, "usd_rows": int(len(usd_df)), "zwg_rows": int(len(zwg_df))},
            },
        ) or step

        # -------------------- TRANSFORMATION --------------------
        step = dbs.TrustlinkDBService.create_step(run_id, "TRANSFORMATION")
        steps["TRANSFORMATION"] = step
        active_step_name = "TRANSFORMATION"
        active_step_id = step["id"]
        s_start = _now_utc()
        dbs.TrustlinkDBService.update_step(
            step["id"],
            {
                "status": "running",
                "started_at": s_start,
                "completed_at": None,
                "duration_ms": 0,
                "row_count": 0,
                "metadata": {"phase": "started"},
            },
        )

        try:
            transformed = cleaning.run_full_transformation(idc_df, usd_df, zwg_df)
        except Exception as exc:
            raise RuntimeError(f"Transformation failed: {exc}") from exc
        combined_df = transformed.get("dataframe")
        transform_metrics = transformed.get("metrics", {})
        if combined_df is None:
            raise ValueError("Transformation returned no dataset")
        combined_df = combined_df[cleaning.FINAL_COLUMNS]

        s_end = _now_utc()
        s_dur = int((s_end - s_start).total_seconds() * 1000)
        transform_duration_ms = s_dur
        steps["TRANSFORMATION"] = dbs.TrustlinkDBService.update_step(step["id"], {"status": "completed", "row_count": int(transform_metrics.get("total_rows", 0)), "duration_ms": s_dur, "completed_at": s_end, "metadata": transform_metrics}) or step

        # -------------------- VALIDATION --------------------
        step = dbs.TrustlinkDBService.create_step(run_id, "VALIDATION")
        steps["VALIDATION"] = step
        active_step_name = "VALIDATION"
        active_step_id = step["id"]
        s_start = _now_utc()
        dbs.TrustlinkDBService.update_step(
            step["id"],
            {
                "status": "running",
                "started_at": s_start,
                "completed_at": None,
                "duration_ms": 0,
                "row_count": 0,
                "metadata": {"phase": "started"},
            },
        )

        try:
            validation_report = cleaning.generate_integrity_report(combined_df, path=None)
        except Exception as exc:
            raise RuntimeError(f"Validation failed: {exc}") from exc
        validation_metrics = validation_report.get("metrics", {})

        s_end = _now_utc()
        s_dur = int((s_end - s_start).total_seconds() * 1000)
        validation_duration_ms = s_dur
        steps["VALIDATION"] = dbs.TrustlinkDBService.update_step(step["id"], {"status": "completed", "row_count": int(validation_metrics.get("total_rows", 0)), "duration_ms": s_dur, "completed_at": s_end, "metadata": validation_report}) or step

        # -------------------- FILE_SAVE --------------------
        step = dbs.TrustlinkDBService.create_step(run_id, "FILE_SAVE")
        steps["FILE_SAVE"] = step
        active_step_name = "FILE_SAVE"
        active_step_id = step["id"]
        s_start = _now_utc()
        dbs.TrustlinkDBService.update_step(
            step["id"],
            {
                "status": "running",
                "started_at": s_start,
                "completed_at": None,
                "duration_ms": 0,
                "row_count": 0,
                "metadata": {"phase": "started"},
            },
        )

        # ensure directory exists
        file_dir = Path("static") / "trustlink"
        file_dir.mkdir(parents=True, exist_ok=True)

        # Match legacy/manual output contract exactly:
        # - filename prefix: STPLINK_AGRI_ACC_
        # - no file extension
        filename = f"STPLINK_AGRI_ACC_{today.strftime('%Y%m%d')}"
        file_path = file_dir / filename

        # Match manual pipeline format:
        # - CSV payload
        # - header excluded
        # - no index
        # - UTF-8 encoding
        combined_df.to_csv(file_path, index=False, header=False, encoding="utf-8")

        # compute md5
        md5 = hashlib.md5()
        with open(file_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(8192), b""):
                md5.update(chunk)
        file_hash = md5.hexdigest()
        file_size_bytes = int(file_path.stat().st_size)

        s_end = _now_utc()
        s_dur = int((s_end - s_start).total_seconds() * 1000)
        steps["FILE_SAVE"] = dbs.TrustlinkDBService.update_step(
            step["id"],
            {
                "status": "completed",
                "row_count": int(len(combined_df)),
                "duration_ms": s_dur,
                "completed_at": s_end,
                "metadata": {"file": str(file_path), "file_hash": file_hash, "file_size_bytes": file_size_bytes},
            },
        ) or step
        active_step_name = None
        active_step_id = None

        # -------------------- Update run -> success --------------------
        total_duration = 0
        try:
            started_at = run.get("started_at")
            if isinstance(started_at, str):
                # parse ISO
                started_at = datetime.fromisoformat(started_at)
            total_duration = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000) if started_at else 0
        except Exception:
            total_duration = 0

        update_fields = {
            "status": "success",
            "completed_at": _now_utc(),
            "file_path": str(file_path),
            "file_hash": file_hash,
            "total_rows": int(validation_metrics.get("total_rows", 0)),
            "idc_rows": idc_count,
            "digipay_rows": digipay_count,
            "extract_duration_ms": int(extract_duration_ms),
            "transform_duration_ms": int(transform_duration_ms),
            "validation_duration_ms": int(validation_duration_ms),
            "total_duration_ms": total_duration,
            "error_message": " | ".join(run_warnings) if run_warnings else None,
        }

        # Note: steps dict entries may not include duration_ms; fetch from DB if needed
        dbs.TrustlinkDBService.update_run(run_id, update_fields)
        dbs.TrustlinkDBService.update_step_metadata(
            run_id,
            "FILE_SAVE",
            {"file_size_bytes": file_size_bytes},
        )

        # --- Notify admins/managers and send email (success) ---
        try:
            title = f"✅ Trustlink Extraction Success - {today.isoformat()}"
            message = (
                f"Run ID: {run_id}\n"
                f"Status: success\n"
                f"Total rows: {update_fields.get('total_rows')}\n"
                f"IDC rows: {update_fields.get('idc_rows')}\n"
                f"Digipay rows: {update_fields.get('digipay_rows')}\n"
                f"Extract duration (ms): {update_fields.get('extract_duration_ms')}\n"
                f"Transform duration (ms): {update_fields.get('transform_duration_ms')}\n"
                f"Validation duration (ms): {update_fields.get('validation_duration_ms')}\n"
                f"Total duration (ms): {update_fields.get('total_duration_ms')}\n"
                f"File: {update_fields.get('file_path')}\n"
                f"File hash (md5): {file_hash}\n"
                f"File size (bytes): {file_size_bytes}"
            )
            # create DB notifications for admin/manager roles
            try:
                NotificationDBService.notify_admin_and_managers(
                    title=title,
                    message=message,
                    related_entity="trustlink_run",
                    related_id=UUIDType(run_id) if run_id else None,
                )
            except Exception as e:
                log.error(f"Failed to create system notification for trustlink success: {e}")

            # send email (fire-and-forget) — use TRUSTLINK_NOTIFICATION_EMAILS if provided
            try:
                env_emails = os.getenv("TRUSTLINK_NOTIFICATION_EMAILS")
                if env_emails:
                    recipients = [e.strip() for e in env_emails.split(",") if e.strip()]
                else:
                    recipients = [SMTP_FROM]

                send_email_fire_and_forget(recipients, title, message)
            except Exception:
                log.exception("Failed to schedule success email for trustlink run")
        except Exception:
            log.exception("Failed while sending success notifications for trustlink run")

        return {"status": "SUCCESS", "run_id": run_id, "file_path": str(file_path), "file_hash": file_hash}

    except Exception as e:
        if active_step_id is not None:
            try:
                dbs.TrustlinkDBService.update_step(
                    active_step_id,
                    {
                        "status": "failed",
                        "completed_at": _now_utc(),
                        "metadata": {"error": str(e), "failed_step": active_step_name},
                    },
                )
            except Exception:
                log.exception(f"Failed to mark trustlink step as failed: {active_step_name}")
        # mark run failed and record error
        try:
            fail_update = {"status": "failed", "error_message": str(e), "completed_at": _now_utc()}
            if active_step_name:
                fail_update["error_message"] = f"[{active_step_name}] {str(e)}"
            if run_id is not None:
                dbs.TrustlinkDBService.update_run(run_id, fail_update)
        except Exception:
            pass
        log.error(f"Trustlink extraction failed: {e}")
        # Notify admins/managers and email on failure
        try:
            title = f"🚨 Trustlink Extraction FAILED - {date.today().isoformat()}"
            message = (
                f"Run ID: {run_id}\n"
                f"Status: failed\n"
                f"Failed step: {active_step_name}\n"
                f"Error: {str(e)}"
            )
            try:
                NotificationDBService.notify_admin_and_managers(
                    title=title,
                    message=message,
                    related_entity="trustlink_run",
                    related_id=UUIDType(run_id) if run_id else None,
                )
            except Exception as ne:
                log.error(f"Failed to create system notification for trustlink failure: {ne}")

            try:
                env_emails = os.getenv("TRUSTLINK_NOTIFICATION_EMAILS")
                if env_emails:
                    recipients = [e.strip() for e in env_emails.split(",") if e.strip()]
                else:
                    recipients = [SMTP_FROM]

                send_email_fire_and_forget(recipients, title, message)
            except Exception:
                log.exception("Failed to schedule failure email for trustlink run")
        except Exception:
            log.exception("Failed while sending failure notifications for trustlink run")

        return {"status": "FAILED", "run_id": run_id, "error": str(e)}


class TrustlinkWorkflow:
    """Compatibility wrapper exposing workflow functions as methods."""
    @staticmethod
    def run_extraction(run_type: str = "manual", triggered_by: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
        return run_extraction(run_type=run_type, triggered_by=triggered_by, force=force)
