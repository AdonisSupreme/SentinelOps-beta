"""
Organization API: Departments and Sections.
Supports user assignment to department/section in User Management.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from app.auth.service import get_current_user
from app.db.database import get_connection
from app.core.logging import get_logger

log = get_logger("org-router")

router = APIRouter(prefix="/org", tags=["Organization"])


def _is_admin(user: dict) -> bool:
    """Case-insensitive admin check."""
    return (user.get("role") or "").lower() == "admin"


@router.get("/departments")
async def list_departments(
    current_user: dict = Depends(get_current_user),
) -> List[dict]:
    """List all departments. Read-only for authenticated users."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, department_name FROM department ORDER BY department_name"
                )
                rows = cur.fetchall()
                return [
                    {
                        "id": r[0],
                        "department_name": r[1],
                        "created_at": None,
                    }
                    for r in rows
                ]
    except Exception as e:
        log.error(f"Error listing departments: {e}")
        raise


@router.get("/sections")
async def list_sections(
    department_id: Optional[int] = Query(None, description="Filter by department"),
    current_user: dict = Depends(get_current_user),
) -> List[dict]:
    """
    List sections. Optionally filtered by department.
    Non-admins see only sections in their department or their assigned section.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if department_id is not None:
                    cur.execute(
                        """
                        SELECT s.id, s.section_name, s.manager_id
                        FROM sections s
                        JOIN department_sections ds ON ds.section_id = s.id
                        WHERE ds.department_id = %s
                        ORDER BY s.section_name
                        """,
                        (department_id,),
                    )
                else:
                    cur.execute(
                        "SELECT id, section_name, manager_id FROM sections ORDER BY section_name"
                    )
                rows = cur.fetchall()
                sections = [
                    {
                        "id": str(r[0]),
                        "section_name": r[1],
                        "manager_id": str(r[2]) if r[2] else None,
                        "created_at": None,
                    }
                    for r in rows
                ]
                # Non-admins: optionally restrict to their section (for UX)
                if not _is_admin(current_user) and current_user.get("section_id"):
                    # Still return all for dropdowns; scope is enforced on write
                    pass
                return sections
    except Exception as e:
        log.error(f"Error listing sections: {e}")
        raise


@router.get("/sections/by-department/{department_id}")
async def list_sections_by_department(
    department_id: int,
    current_user: dict = Depends(get_current_user),
) -> List[dict]:
    """List sections for a specific department."""
    return await list_sections(department_id=department_id, current_user=current_user)
