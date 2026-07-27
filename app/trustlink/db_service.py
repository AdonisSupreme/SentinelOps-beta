"""Database service for Trustlink audit tables.

Follows the same raw-SQL, psycopg connection patterns used by
`app.notifications.db_service.NotificationDBService`.

This module contains only thin DB wrappers (no business logic).
"""

from typing import Optional, List, Dict, Any
from uuid import UUID, uuid4
from datetime import datetime, timezone, date, timedelta
import os
import json
from pathlib import Path
from psycopg.types.json import Json

from psycopg2 import Error as _PgError

from app.db.database import get_connection
from app.core.logging import get_logger

log = get_logger("trustlink-db-service")


class TrustlinkDBService:
    """DB access helpers for trustlink_runs and trustlink_steps."""

    FILE_RETENTION_DAYS = 2
    PIPELINE_CONFIG_KEY = "account-extraction"

    @staticmethod
    def _adapt_param(field_name: str, value: Any) -> Any:
        """Adapt Python values for psycopg placeholders."""
        if field_name == "metadata" and isinstance(value, dict):
            return Json(value)
        return value

    @staticmethod
    def _emit_realtime_update(payload: Dict[str, Any]) -> None:
        """Broadcast Trustlink changes through the existing checklist websocket."""
        try:
            from app.services.websocket import broadcast_checklist_update
            import asyncio
            import threading

            async def _emit_update():
                try:
                    await broadcast_checklist_update({
                        "type": "trustlink_update",
                        **payload,
                    })
                except Exception as exc:
                    log.debug(f"Failed to broadcast trustlink realtime update: {exc}")

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_emit_update())
            except RuntimeError:
                threading.Thread(target=lambda: asyncio.run(_emit_update()), daemon=True).start()
        except Exception as exc:
            log.debug(f"Trustlink websocket broadcast skipped: {exc}")

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        """Convert DB numeric values to int with null/invalid safety."""
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _coerce_run_date(run_date_value: Optional[str | date]) -> Optional[date]:
        if not run_date_value:
            return None
        if isinstance(run_date_value, date):
            return run_date_value
        try:
            return date.fromisoformat(str(run_date_value))
        except ValueError:
            return None

    @staticmethod
    def _duration_ms_between(started_at: Optional[datetime], completed_at: Optional[datetime]) -> int:
        if not started_at or not completed_at:
            return 0

        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        if completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=timezone.utc)

        return max(0, int((completed_at - started_at).total_seconds() * 1000))

    @staticmethod
    def _trustlink_static_root() -> Path:
        return (Path(__file__).resolve().parents[2] / "static" / "trustlink").resolve()

    @staticmethod
    def _resolve_export_path(file_path: str) -> Path:
        resolved = Path(file_path).resolve()
        static_root = TrustlinkDBService._trustlink_static_root()
        if static_root not in resolved.parents and resolved.parent != static_root:
            raise ValueError("Refusing to access files outside the Trustlink export directory")
        return resolved

    @staticmethod
    def _resolve_triggered_by_display(triggered_by: Optional[str], run_type: Optional[str]) -> str:
        if run_type == "scheduled" and not triggered_by:
            return "System"
        if not triggered_by:
            return "Unknown"

        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT username, first_name, last_name
                        FROM users
                        WHERE CAST(id AS TEXT) = %s
                        LIMIT 1
                        """,
                        (str(triggered_by),),
                    )
                    row = cur.fetchone()
                    if row:
                        username = row[0]
                        first_name = row[1] or ""
                        last_name = row[2] or ""
                        full_name = f"{first_name} {last_name}".strip()
                        return username if not full_name else f"{username} ({full_name})"
        except Exception as e:
            log.debug(f"Failed to resolve triggered_by display for {triggered_by}: {e}")

        return "System" if run_type == "scheduled" else str(triggered_by)

    @staticmethod
    def _compute_file_fields(file_path: Optional[str]) -> Dict[str, Any]:
        if not file_path:
            return {
                "file_name": None,
                "file_status": "not_generated",
                "file_present": False,
            }

        file_name = Path(file_path).name
        exists = Path(file_path).exists()
        return {
            "file_name": file_name,
            "file_status": "available" if exists else "deleted",
            "file_present": exists,
        }

    @staticmethod
    def _can_delete_file_for_run(run_date_value: Optional[str | date]) -> bool:
        run_date_value = TrustlinkDBService._coerce_run_date(run_date_value)
        if not run_date_value:
            return False
        cutoff = date.today() - timedelta(days=TrustlinkDBService.FILE_RETENTION_DAYS)
        return run_date_value <= cutoff

    @staticmethod
    def _get_latest_available_file_run() -> Optional[Dict[str, Any]]:
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT *
                        FROM trustlink_runs
                        WHERE file_path IS NOT NULL
                        ORDER BY run_date DESC, completed_at DESC NULLS LAST, created_at DESC
                        """
                    )
                    rows = cur.fetchall()
                    for row in rows:
                        run = TrustlinkDBService._enrich_run_dict(TrustlinkDBService._row_to_run_dict(row))
                        if run.get("file_present"):
                            return run
        except Exception as e:
            log.error(f"Failed to resolve latest available trustlink file run: {e}")
        return None

    @staticmethod
    def _has_newer_available_file(run_date_value: Optional[str | date], run_id: UUID | str) -> bool:
        run_date_value = TrustlinkDBService._coerce_run_date(run_date_value)
        latest = TrustlinkDBService._get_latest_available_file_run()
        latest_date = TrustlinkDBService._coerce_run_date(latest.get("run_date") if latest else None)

        return bool(
            latest
            and latest_date
            and run_date_value
            and str(latest.get("id")) != str(run_id)
            and latest_date > run_date_value
        )

    @staticmethod
    def _enrich_run_dict(run: Dict[str, Any]) -> Dict[str, Any]:
        if not run:
            return run
        enriched = dict(run)
        enriched["triggered_by_display"] = TrustlinkDBService._resolve_triggered_by_display(
            enriched.get("triggered_by"),
            enriched.get("run_type"),
        )
        enriched.update(TrustlinkDBService._compute_file_fields(enriched.get("file_path")))
        return enriched

    @staticmethod
    def _row_to_pipeline_config(row: tuple) -> Dict[str, Any]:
        config_key, idc_enabled, digipay_enabled, updated_by, updated_at = row
        return {
            "config_key": config_key,
            "idc_enabled": bool(idc_enabled),
            "digipay_enabled": bool(digipay_enabled),
            "updated_by": updated_by,
            "updated_at": updated_at.isoformat() if updated_at else None,
        }

    @staticmethod
    def get_pipeline_config() -> Dict[str, Any]:
        """Return the active source route used by the next extraction run."""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT config_key, idc_enabled, digipay_enabled, updated_by, updated_at
                    FROM trustlink_pipeline_config
                    WHERE config_key = %s
                    """,
                    (TrustlinkDBService.PIPELINE_CONFIG_KEY,),
                )
                row = cur.fetchone()

        if not row:
            raise RuntimeError("TrustLink pipeline configuration is not initialized")
        return TrustlinkDBService._row_to_pipeline_config(row)

    @staticmethod
    def update_pipeline_config(
        *,
        idc_enabled: bool,
        digipay_enabled: bool,
        changed_by: str,
    ) -> Dict[str, Any]:
        """Atomically update source routing and preserve an immutable audit row."""
        if not idc_enabled and not digipay_enabled:
            raise ValueError("At least one TrustLink ingest source must remain enabled")

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT config_key, idc_enabled, digipay_enabled, updated_by, updated_at
                    FROM trustlink_pipeline_config
                    WHERE config_key = %s
                    FOR UPDATE
                    """,
                    (TrustlinkDBService.PIPELINE_CONFIG_KEY,),
                )
                current = cur.fetchone()
                if not current:
                    raise RuntimeError("TrustLink pipeline configuration is not initialized")

                current_config = TrustlinkDBService._row_to_pipeline_config(current)
                if (
                    current_config["idc_enabled"] == idc_enabled
                    and current_config["digipay_enabled"] == digipay_enabled
                ):
                    return current_config

                cur.execute(
                    """
                    UPDATE trustlink_pipeline_config
                    SET idc_enabled = %s,
                        digipay_enabled = %s,
                        updated_by = %s,
                        updated_at = now()
                    WHERE config_key = %s
                    RETURNING config_key, idc_enabled, digipay_enabled, updated_by, updated_at
                    """,
                    (
                        idc_enabled,
                        digipay_enabled,
                        changed_by,
                        TrustlinkDBService.PIPELINE_CONFIG_KEY,
                    ),
                )
                updated = cur.fetchone()
                cur.execute(
                    """
                    INSERT INTO trustlink_pipeline_config_audit (
                        previous_idc_enabled,
                        previous_digipay_enabled,
                        idc_enabled,
                        digipay_enabled,
                        changed_by
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        current_config["idc_enabled"],
                        current_config["digipay_enabled"],
                        idc_enabled,
                        digipay_enabled,
                        changed_by,
                    ),
                )
                conn.commit()

        config = TrustlinkDBService._row_to_pipeline_config(updated)
        TrustlinkDBService._emit_realtime_update({
            "event": "pipeline_config",
            **config,
        })
        return config

    @staticmethod
    def create_run(run_data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert a new trustlink_runs row. Returns the inserted row as dict."""
        # new behaviour: attempt insert, but handle unique constraint (23505)
        run_id = uuid4()
        run_date = run_data.get("run_date")
        force = bool(run_data.get("force", False))

        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO trustlink_runs (
                            id, run_date, run_type, triggered_by, status,
                            started_at, completed_at,
                            file_path, file_hash, integrity_report_path,
                            total_rows, idc_rows, digipay_rows,
                            extract_duration_ms, transform_duration_ms, validation_duration_ms, total_duration_ms,
                            error_message, created_at
                        ) VALUES (
                            %s, %s, %s, %s, %s,
                            %s, %s,
                            %s, %s, %s,
                            %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s
                        ) RETURNING *
                        """,
                        (
                            run_id,
                            run_date,
                            run_data.get("run_type"),
                            run_data.get("triggered_by"),
                            run_data.get("status"),
                            run_data.get("started_at", datetime.now(timezone.utc)),
                            run_data.get("completed_at"),
                            run_data.get("file_path"),
                            run_data.get("file_hash"),
                            run_data.get("integrity_report_path"),
                            run_data.get("total_rows", 0),
                            run_data.get("idc_rows", 0),
                            run_data.get("digipay_rows", 0),
                            run_data.get("extract_duration_ms", 0),
                            run_data.get("transform_duration_ms", 0),
                            run_data.get("validation_duration_ms", 0),
                            run_data.get("total_duration_ms", 0),
                            run_data.get("error_message"),
                            run_data.get("created_at", datetime.now(timezone.utc)),
                        ),
                    )

                    row = cur.fetchone()
                    conn.commit()

                    if row:
                        log.info(f"Created trustlink run {run_id}")
                        return {"created": True, "run": TrustlinkDBService._enrich_run_dict(TrustlinkDBService._row_to_run_dict(row))}

        except Exception as e:
            # Handle Postgres unique violation: another run for this date exists
            pgcode = getattr(e, 'pgcode', None)
            msg = str(e)
            if pgcode == '23505' or 'duplicate key value' in msg.lower():
                log.info(f"Trustlink run for date {run_date} already exists (conflict): {e}")
                # fetch existing run
                existing = TrustlinkDBService.get_run_by_date(run_date)
                if not existing:
                    log.error(f"Unique violation but failed to read existing run for date {run_date}")
                    raise

                existing_id = existing.get('id')

                if force:
                    # perform overwrite: delete old file and steps, then update run in-place
                    try:
                        with get_connection() as conn:
                            with conn.cursor() as cur:
                                # delete steps for run
                                cur.execute("DELETE FROM trustlink_steps WHERE run_id = %s", (existing_id,))
                                conn.commit()
                    except Exception:
                        log.exception(f"Failed to delete steps for run {existing_id} during overwrite")

                    # remove old file from disk if present
                    try:
                        old_fp = existing.get('file_path')
                        if old_fp:
                            try:
                                if os.path.exists(old_fp):
                                    os.remove(old_fp)
                                    log.info(f"Removed old trustlink file during overwrite: {old_fp}")
                            except Exception:
                                log.exception(f"Failed to remove old trustlink file: {old_fp}")
                    except Exception:
                        log.debug("No file to remove or error inspecting file path")

                    # update existing run row to reset for new run execution
                    try:
                        reset_fields = {
                            'status': run_data.get('status', 'running'),
                            'started_at': run_data.get('started_at', datetime.now(timezone.utc)),
                            'completed_at': None,
                            'file_path': None,
                            'file_hash': None,
                            'integrity_report_path': None,
                            'total_rows': 0,
                            'idc_rows': 0,
                            'digipay_rows': 0,
                            'extract_duration_ms': 0,
                            'transform_duration_ms': 0,
                            'validation_duration_ms': 0,
                            'total_duration_ms': 0,
                            'error_message': None,
                        }
                        updated = TrustlinkDBService.update_run(existing_id, reset_fields)
                        return {"created": False, "run": updated, "overwritten": True}
                    except Exception:
                        log.exception(f"Failed to reset existing run {existing_id} during overwrite")
                        raise

                # not forcing: return existing run info
                return {"created": False, "run": existing}

            # unexpected exception — re-raise after logging
            log.error(f"Failed to create trustlink run: {e}")
            raise

    @staticmethod
    def update_run(run_id: UUID, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update fields on a run row. Returns updated row or None."""
        if not fields:
            return None

        set_clauses = []
        params: List[Any] = []

        for idx, (k, v) in enumerate(fields.items(), start=1):
            set_clauses.append(f"{k} = %s")
            params.append(v)

        params.append(run_id)

        sql = f"UPDATE trustlink_runs SET {', '.join(set_clauses)} WHERE id = %s RETURNING *"

        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    row = cur.fetchone()
                    conn.commit()

                    if row:
                        log.info(f"Updated trustlink run {run_id}")
                        run_dict = TrustlinkDBService._enrich_run_dict(TrustlinkDBService._row_to_run_dict(row))
                        TrustlinkDBService._emit_realtime_update({
                            "event": "run",
                            "run_id": str(run_id),
                            "run_status": run_dict.get("status"),
                            "status": run_dict.get("status"),
                            "total_rows": run_dict.get("total_rows"),
                            "total_duration_ms": run_dict.get("total_duration_ms"),
                            "file_present": run_dict.get("file_present"),
                            "file_status": run_dict.get("file_status"),
                            "completed_at": run_dict.get("completed_at"),
                        })
                        return run_dict
        except Exception as e:
            log.error(f"Failed to update trustlink run {run_id}: {e}")
            raise

        return None

    @staticmethod
    def create_step(run_id: UUID, step_name: str) -> Dict[str, Any]:
        """Insert a new step for a run and return the inserted row."""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO trustlink_steps (
                            run_id, step_name, status, row_count, duration_ms, metadata, started_at, completed_at, created_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s
                        ) RETURNING *
                        """,
                        (
                            run_id,
                            step_name,
                            "pending",
                            0,
                            0,
                            Json({}),
                            None,
                            None,
                            datetime.now(timezone.utc),
                        ),
                    )

                    row = cur.fetchone()
                    conn.commit()

                    if row:
                        log.info(f"Created trustlink step '{step_name}' for run {run_id}")
                        return TrustlinkDBService._row_to_step_dict(row)

        except Exception as e:
            log.error(f"Failed to create trustlink step for run {run_id}: {e}")
            raise

    @staticmethod
    def update_step(step_id: int, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update a step row. Returns updated row or None."""
        if not fields:
            return None

        set_clauses = []
        params: List[Any] = []

        for idx, (k, v) in enumerate(fields.items(), start=1):
            set_clauses.append(f"{k} = %s")
            params.append(TrustlinkDBService._adapt_param(k, v))

        params.append(step_id)

        sql = f"UPDATE trustlink_steps SET {', '.join(set_clauses)} WHERE id = %s RETURNING *"

        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    row = cur.fetchone()
                    conn.commit()

                    if row:
                        log.info(f"Updated trustlink step {step_id}")
                        step_dict = TrustlinkDBService._row_to_step_dict(row)

                        TrustlinkDBService._emit_realtime_update({
                            "event": "step",
                            "run_id": step_dict.get("run_id"),
                            "step_id": step_dict.get("id"),
                            "step": step_dict.get("step_name"),
                            "step_status": step_dict.get("status"),
                            "status": step_dict.get("status"),
                            "row_count": step_dict.get("row_count"),
                            "duration_ms": step_dict.get("duration_ms"),
                            "started_at": step_dict.get("started_at"),
                            "completed_at": step_dict.get("completed_at"),
                        })

                        return step_dict
        except Exception as e:
            log.error(f"Failed to update trustlink step {step_id}: {e}")
            raise

        return None

    @staticmethod
    def get_run_by_date(run_date) -> Optional[Dict[str, Any]]:
        """Return a run for a given date."""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM trustlink_runs WHERE run_date = %s", (run_date,))
                    row = cur.fetchone()
                    if row:
                        return TrustlinkDBService._enrich_run_dict(TrustlinkDBService._row_to_run_dict(row))
        except Exception as e:
            log.error(f"Failed to get trustlink run by date {run_date}: {e}")
            return None

        return None

    @staticmethod
    def get_run_by_id(run_id: UUID) -> Optional[Dict[str, Any]]:
        """Return a run by id."""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM trustlink_runs WHERE id = %s", (run_id,))
                    row = cur.fetchone()
                    if row:
                        return TrustlinkDBService._enrich_run_dict(TrustlinkDBService._row_to_run_dict(row))
        except Exception as e:
            log.error(f"Failed to get trustlink run by id {run_id}: {e}")
            return None

        return None

    @staticmethod
    def list_runs(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """List runs ordered by run_date desc."""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM trustlink_runs ORDER BY run_date DESC LIMIT %s OFFSET %s",
                        (limit, offset),
                    )
                    rows = cur.fetchall()

                    return [TrustlinkDBService._enrich_run_dict(TrustlinkDBService._row_to_run_dict(r)) for r in rows]
        except Exception as e:
            log.error(f"Failed to list trustlink runs: {e}")
            return []

    @staticmethod
    def delete_run_file(run_id: UUID) -> Dict[str, Any]:
        run = TrustlinkDBService.get_run_by_id(run_id)
        if not run:
            raise ValueError("Trustlink run not found")

        if not run.get("file_path"):
            return {
                "deleted": False,
                "run_id": str(run_id),
                "file_status": "not_generated",
                "detail": "This run does not have a saved file.",
            }

        if run.get("file_status") == "deleted":
            return {
                "deleted": False,
                "run_id": str(run_id),
                "file_status": "deleted",
                "detail": "The saved file was already removed from disk.",
            }

        if not TrustlinkDBService._can_delete_file_for_run(run.get("run_date")):
            raise ValueError("Only files at least two days old can be deleted")

        if not TrustlinkDBService._has_newer_available_file(run.get("run_date"), run_id):
            raise ValueError("A newer Trustlink export must exist before this file can be deleted")

        resolved = TrustlinkDBService._resolve_export_path(run["file_path"])
        try:
            if resolved.exists():
                resolved.unlink()
        except Exception as e:
            log.error(f"Failed to delete trustlink file for run {run_id}: {e}")
            raise

        return {
            "deleted": True,
            "run_id": str(run_id),
            "file_status": "deleted",
            "detail": f"Deleted saved file '{resolved.name}' while preserving the run audit record.",
        }

    @staticmethod
    def prune_old_export_files(latest_run_id: Optional[UUID | str] = None) -> Dict[str, Any]:
        """Delete old export files only after a newer latest export exists.

        Run audit rows and file_path metadata are preserved so history remains
        visible while file_status naturally reports deleted from disk.
        """
        latest = None
        if latest_run_id:
            latest = TrustlinkDBService.get_run_by_id(latest_run_id)
            if latest and not latest.get("file_present"):
                latest = None
        if not latest:
            latest = TrustlinkDBService._get_latest_available_file_run()

        latest_date = TrustlinkDBService._coerce_run_date(latest.get("run_date") if latest else None)
        if not latest or not latest_date or not latest.get("file_present"):
            return {
                "deleted_count": 0,
                "skipped_count": 0,
                "latest_run_id": None,
                "detail": "No latest available export exists; pruning skipped.",
            }

        cutoff = latest_date - timedelta(days=TrustlinkDBService.FILE_RETENTION_DAYS)
        deleted: list[str] = []
        skipped: list[str] = []

        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT *
                        FROM trustlink_runs
                        WHERE file_path IS NOT NULL
                          AND id <> %s
                          AND run_date <= %s
                        ORDER BY run_date ASC
                        """,
                        (latest.get("id"), cutoff),
                    )
                    rows = cur.fetchall()

            for row in rows:
                candidate = TrustlinkDBService._enrich_run_dict(TrustlinkDBService._row_to_run_dict(row))
                file_path = candidate.get("file_path")
                if not file_path or not candidate.get("file_present"):
                    skipped.append(str(candidate.get("id")))
                    continue

                try:
                    resolved = TrustlinkDBService._resolve_export_path(file_path)
                    if resolved.exists():
                        resolved.unlink()
                        deleted.append(str(candidate.get("id")))
                    else:
                        skipped.append(str(candidate.get("id")))
                except Exception as e:
                    skipped.append(str(candidate.get("id")))
                    log.error(f"Failed to prune old trustlink export {candidate.get('id')}: {e}")
        except Exception as e:
            log.error(f"Failed to prune old trustlink exports: {e}")
            raise

        return {
            "deleted_count": len(deleted),
            "skipped_count": len(skipped),
            "latest_run_id": str(latest.get("id")),
            "cutoff_date": cutoff.isoformat(),
            "deleted_run_ids": deleted,
            "skipped_run_ids": skipped,
        }

    @staticmethod
    def list_steps(run_id: UUID) -> List[Dict[str, Any]]:
        """List steps for a run ordered by creation sequence."""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM trustlink_steps WHERE run_id = %s ORDER BY id ASC",
                        (run_id,),
                    )
                    rows = cur.fetchall()
                    return [TrustlinkDBService._row_to_step_dict(r) for r in rows]
        except Exception as e:
            log.error(f"Failed to list trustlink steps for run {run_id}: {e}")
            return []

    @staticmethod
    def update_step_metadata(run_id: UUID, step_name: str, metadata_patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Merge metadata into the latest step row for a given run and step name."""
        if not metadata_patch:
            return None
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE trustlink_steps
                        SET metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
                        WHERE id = (
                            SELECT id FROM trustlink_steps
                            WHERE run_id = %s AND step_name = %s
                            ORDER BY id DESC LIMIT 1
                        )
                        RETURNING *
                        """,
                        (json.dumps(metadata_patch), run_id, step_name),
                    )
                    row = cur.fetchone()
                    conn.commit()
                    if row:
                        return TrustlinkDBService._row_to_step_dict(row)
        except Exception as e:
            log.error(f"Failed to update trustlink step metadata for run {run_id}, step {step_name}: {e}")
            return None

    # -------------------- helpers --------------------
    @staticmethod
    def _row_to_run_dict(row: tuple) -> Dict[str, Any]:
        # trustlink_runs column order as created in migration
        (
            id,
            run_date,
            run_type,
            triggered_by,
            status,
            started_at,
            completed_at,
            file_path,
            file_hash,
            integrity_report_path,
            total_rows,
            idc_rows,
            digipay_rows,
            extract_duration_ms,
            transform_duration_ms,
            validation_duration_ms,
            total_duration_ms,
            error_message,
            created_at,
        ) = row

        normalized_total_duration_ms = TrustlinkDBService._to_int(total_duration_ms)
        if normalized_total_duration_ms <= 0:
            normalized_total_duration_ms = TrustlinkDBService._duration_ms_between(started_at, completed_at)

        return {
            'id': str(id) if id else None,
            'run_date': run_date.isoformat() if run_date else None,
            'run_type': run_type,
            'triggered_by': triggered_by,
            'status': status,
            'started_at': started_at.isoformat() if started_at else None,
            'completed_at': completed_at.isoformat() if completed_at else None,
            'file_path': file_path,
            'file_hash': file_hash,
            'integrity_report_path': integrity_report_path,
            'total_rows': TrustlinkDBService._to_int(total_rows),
            'idc_rows': TrustlinkDBService._to_int(idc_rows),
            'digipay_rows': TrustlinkDBService._to_int(digipay_rows),
            'extract_duration_ms': TrustlinkDBService._to_int(extract_duration_ms),
            'transform_duration_ms': TrustlinkDBService._to_int(transform_duration_ms),
            'validation_duration_ms': TrustlinkDBService._to_int(validation_duration_ms),
            'total_duration_ms': normalized_total_duration_ms,
            'error_message': error_message,
            'created_at': created_at.isoformat() if created_at else None,
        }

    @staticmethod
    def _row_to_step_dict(row: tuple) -> Dict[str, Any]:
        (
            id,
            run_id,
            step_name,
            status,
            row_count,
            duration_ms,
            metadata,
            started_at,
            completed_at,
            created_at,
        ) = row

        return {
            'id': id,
            'run_id': str(run_id) if run_id else None,
            'step_name': step_name,
            'status': status,
            'row_count': TrustlinkDBService._to_int(row_count),
            'duration_ms': TrustlinkDBService._to_int(duration_ms),
            'metadata': metadata,
            'started_at': started_at.isoformat() if started_at else None,
            'completed_at': completed_at.isoformat() if completed_at else None,
            'created_at': created_at.isoformat() if created_at else None,
        }
