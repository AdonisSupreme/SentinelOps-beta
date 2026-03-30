"""Database service for Trustlink audit tables.

Follows the same raw-SQL, psycopg connection patterns used by
`app.notifications.db_service.NotificationDBService`.

This module contains only thin DB wrappers (no business logic).
"""

from typing import Optional, List, Dict, Any
from uuid import UUID, uuid4
from datetime import datetime, timezone
import os
import json
from psycopg.types.json import Json

from psycopg2 import Error as _PgError

from app.db.database import get_connection
from app.core.logging import get_logger

log = get_logger("trustlink-db-service")


class TrustlinkDBService:
    """DB access helpers for trustlink_runs and trustlink_steps."""

    @staticmethod
    def _adapt_param(field_name: str, value: Any) -> Any:
        """Adapt Python values for psycopg placeholders."""
        if field_name == "metadata" and isinstance(value, dict):
            return Json(value)
        return value

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
                        return {"created": True, "run": TrustlinkDBService._row_to_run_dict(row)}

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
                        return TrustlinkDBService._row_to_run_dict(row)
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

                        # Emit websocket broadcast for live pipeline updates
                        try:
                            from app.services.websocket import broadcast_checklist_update
                            import asyncio

                            async def _emit_update():
                                try:
                                    payload = {
                                        "type": "trustlink_update",
                                        "run_id": step_dict.get("run_id"),
                                        "step": step_dict.get("step_name"),
                                        "status": step_dict.get("status")
                                    }
                                    await broadcast_checklist_update(payload)
                                except Exception as _e:
                                    log.debug(f"Failed to broadcast trustlink step update: {_e}")

                            try:
                                loop = asyncio.get_running_loop()
                                loop.create_task(_emit_update())
                            except RuntimeError:
                                # No running loop; run in a new thread
                                def _runner():
                                    import asyncio as _asyncio
                                    _asyncio.run(_emit_update())

                                import threading
                                threading.Thread(target=_runner, daemon=True).start()
                        except Exception as e:
                            log.debug(f"Websocket broadcast skipped: {e}")

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
                        return TrustlinkDBService._row_to_run_dict(row)
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
                        return TrustlinkDBService._row_to_run_dict(row)
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

                    return [TrustlinkDBService._row_to_run_dict(r) for r in rows]
        except Exception as e:
            log.error(f"Failed to list trustlink runs: {e}")
            return []

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
            'total_duration_ms': TrustlinkDBService._to_int(total_duration_ms),
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
