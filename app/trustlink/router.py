"""HTTP router for Trustlink endpoints.

Provides endpoints to start runs, list and fetch run details, and download
generated files. Endpoints use existing auth dependencies and BackgroundTasks
to run extraction asynchronously.
"""

from pathlib import Path
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, status
from fastapi.responses import FileResponse
from datetime import date

from app.core.logging import get_logger
from app.auth.service import get_current_user
from app.trustlink.workflow import TrustlinkWorkflow
from app.trustlink import db_service as dbs
from app.trustlink import schemas as schemas

log = get_logger("trustlink-router")

router = APIRouter(prefix="/trustlink", tags=["trustlink"])


@router.post("/run", response_model=schemas.TrustlinkRunStartResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_trustlink_run(
	background_tasks: BackgroundTasks,
	payload: schemas.TrustlinkRunRequest,
	current_user: dict = Depends(get_current_user)
):
	"""Start a Trustlink extraction run in the background.

	Returns a JSON object with the scheduled run id or existing run info.
	"""
	# Synchronous read-only decision: if today's run exists and not forcing,
	# return options immediately without scheduling duplicate work.
	try:
		triggered_by = current_user.get("id") if current_user else None
		today = date.today()
		existing = dbs.TrustlinkDBService.get_run_by_date(today)
		run_type = payload.run_type
		force = payload.force

		if existing and existing.get("status") == "running":
			if force:
				raise HTTPException(status_code=409, detail="Today's extraction is already running")
			return schemas.TrustlinkRunStartResponse(
				status="exists",
				run_id=existing.get("id"),
				run_type=existing.get("run_type") or run_type,
				triggered_by=triggered_by,
				triggered_by_display=existing.get("triggered_by_display"),
				file_path=existing.get("file_path"),
				file_name=existing.get("file_name"),
				options=["download"] if existing.get("file_present") else [],
				detail="Today's extraction is currently running",
			)

		if existing and not force:
			return schemas.TrustlinkRunStartResponse(
				status="exists",
				run_id=existing.get("id"),
				run_type=existing.get("run_type") or run_type,
				triggered_by=triggered_by,
				triggered_by_display=existing.get("triggered_by_display"),
				file_path=existing.get("file_path"),
				file_name=existing.get("file_name"),
				options=(["download", "overwrite"] if existing.get("file_present") else ["overwrite"]),
			)

		background_tasks.add_task(TrustlinkWorkflow.run_extraction, run_type, triggered_by, force)

		return schemas.TrustlinkRunStartResponse(
			status="scheduled",
			run_type=run_type,
			triggered_by=triggered_by,
			triggered_by_display=current_user.get("username") if current_user else None,
		)
	except Exception as e:
		log.error(f"Failed to schedule trustlink run: {e}")
		raise HTTPException(status_code=500, detail="Failed to schedule trustlink run")


@router.get("/runs/today", response_model=schemas.TrustlinkTodayStatusResponse)
async def get_today_run_status(current_user: dict = Depends(get_current_user)):
	"""Return today's run summary and available user actions."""
	try:
		today = date.today()
		run = dbs.TrustlinkDBService.get_run_by_date(today)
		if not run:
			return schemas.TrustlinkTodayStatusResponse(status="none", run=None, has_file=False, options=[])

		has_file = bool(run.get("file_present"))
		if run.get("status") == "running":
			return schemas.TrustlinkTodayStatusResponse(
				status="running",
				run=run,
				has_file=has_file,
				options=["download"] if has_file else [],
			)

		return schemas.TrustlinkTodayStatusResponse(
			status="exists",
			run=run,
			has_file=has_file,
			options=(["download", "overwrite"] if has_file else ["overwrite"]),
		)
	except Exception as e:
		log.error(f"Failed to fetch today's trustlink run status: {e}")
		raise HTTPException(status_code=500, detail="Failed to fetch today's trustlink run status")


@router.post("/run/overwrite", response_model=schemas.TrustlinkRunStartResponse, status_code=status.HTTP_202_ACCEPTED)
async def overwrite_today_run(
	background_tasks: BackgroundTasks,
	current_user: dict = Depends(get_current_user)
):
	"""Force overwrite today's run and schedule a fresh extraction."""
	try:
		today = date.today()
		existing = dbs.TrustlinkDBService.get_run_by_date(today)
		if existing and existing.get("status") == "running":
			raise HTTPException(status_code=409, detail="Today's extraction is already running")

		triggered_by = current_user.get("id") if current_user else None
		background_tasks.add_task(TrustlinkWorkflow.run_extraction, "manual", triggered_by, True)

		return schemas.TrustlinkRunStartResponse(
			status="scheduled",
			run_id=existing.get("id") if existing else None,
			run_type="manual",
			triggered_by=triggered_by,
			triggered_by_display=current_user.get("username") if current_user else None,
			detail="Overwrite extraction scheduled",
		)
	except HTTPException:
		raise
	except Exception as e:
		log.error(f"Failed to schedule trustlink overwrite run: {e}")
		raise HTTPException(status_code=500, detail="Failed to schedule trustlink overwrite run")


@router.get("/runs", response_model=List[schemas.TrustlinkRunListItem])
async def list_runs(limit: int = 50, offset: int = 0, current_user: dict = Depends(get_current_user)):
	"""List recent Trustlink runs."""
	try:
		runs = dbs.TrustlinkDBService.list_runs(limit=limit, offset=offset)
		return runs
	except Exception as e:
		log.error(f"Failed to list trustlink runs: {e}")
		raise HTTPException(status_code=500, detail="Failed to list trustlink runs")


@router.get("/runs/{run_id}", response_model=schemas.TrustlinkRunDetail)
async def get_run(run_id: UUID, current_user: dict = Depends(get_current_user)):
	"""Get details for a specific run."""
	try:
		run = dbs.TrustlinkDBService.get_run_by_id(run_id)
		if not run:
			raise HTTPException(status_code=404, detail="Trustlink run not found")
		return run
	except HTTPException:
		raise
	except Exception as e:
		log.error(f"Failed to fetch trustlink run {run_id}: {e}")
		raise HTTPException(status_code=500, detail="Failed to fetch trustlink run")


@router.get("/runs/{run_id}/steps", response_model=List[schemas.TrustlinkStep])
async def list_run_steps(run_id: UUID, current_user: dict = Depends(get_current_user)):
	"""List step-level audit records for a run."""
	try:
		run = dbs.TrustlinkDBService.get_run_by_id(run_id)
		if not run:
			raise HTTPException(status_code=404, detail="Trustlink run not found")
		return dbs.TrustlinkDBService.list_steps(run_id)
	except HTTPException:
		raise
	except Exception as e:
		log.error(f"Failed to list trustlink steps for run {run_id}: {e}")
		raise HTTPException(status_code=500, detail="Failed to fetch trustlink steps")


@router.get("/download/{run_id}")
async def download_run_file(run_id: UUID, current_user: dict = Depends(get_current_user)):
	"""Download the file produced by a Trustlink run.

	Serves files only from the `static/trustlink` directory to prevent traversal.
	"""
	try:
		run = dbs.TrustlinkDBService.get_run_by_id(run_id)
		if not run:
			raise HTTPException(status_code=404, detail="Trustlink run not found")

		file_path = run.get("file_path")
		if not file_path:
			raise HTTPException(status_code=404, detail="No file available for this run")

		p = Path(file_path)
		# Ensure file is inside project's static/trustlink
		try:
			resolved = p.resolve()
		except Exception:
			raise HTTPException(status_code=400, detail="Invalid file path")

		static_root = Path(__file__).resolve().parents[2] / "static" / "trustlink"
		if static_root not in resolved.parents and static_root != resolved.parent:
			log.warning(f"Refusing to serve file outside trustlink static: {resolved}")
			raise HTTPException(status_code=403, detail="Forbidden")

		if not resolved.exists():
			raise HTTPException(status_code=404, detail="File not found on server")

		from pathlib import Path as _P
		try:
			file_size = resolved.stat().st_size
		except Exception:
			file_size = None

		headers = {}
		if file_size is not None:
			headers["Content-Length"] = str(file_size)

		filename = run.get("file_name") or resolved.name
		return FileResponse(path=str(resolved), filename=filename, media_type="application/octet-stream", headers=headers)

	except HTTPException:
		raise
	except Exception as e:
		log.error(f"Error serving trustlink file for run {run_id}: {e}")
		raise HTTPException(status_code=500, detail="Failed to serve file")


@router.delete("/runs/{run_id}/file", response_model=schemas.TrustlinkFileDeleteResponse)
async def delete_run_file(run_id: UUID, current_user: dict = Depends(get_current_user)):
	"""Delete an old saved export file while preserving the run audit row."""
	try:
		result = dbs.TrustlinkDBService.delete_run_file(run_id)
		return schemas.TrustlinkFileDeleteResponse(**result)
	except ValueError as e:
		raise HTTPException(status_code=400, detail=str(e))
	except Exception as e:
		log.error(f"Failed to delete trustlink file for run {run_id}: {e}")
		raise HTTPException(status_code=500, detail="Failed to delete trustlink file")
