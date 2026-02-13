from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.schemas import UserResponse
from app.auth.service import get_current_user
from app.core.authorization import is_admin
from app.core.logging import get_logger
from app.core.security import hash_password
from app.db.database import get_connection
from app.users.schemas import UserCreate, UserUpdate, UserListItem

log = get_logger("users-router")

router = APIRouter(prefix="/users", tags=["Users"])


def _normalize_role_name(role: str) -> str:
    """
    Map business-facing role labels to concrete DB role names.

    The UI works with three primary roles:
      - admin
      - manager
      - user

    These are mapped to the existing roles table:
      - admin      → admin
      - manager    → manager
      - user       → user
    """

    if not role:
        raise ValueError("Role is required")

    value = role.strip().lower()
    if value in {"admin", "administrator"}:
        return "admin"
    if value in {"manager", "supervisor"}:
        return "manager"
    if value in {"user", "operator", "participant", "staff"}:
        return "user"

    # Fallback: allow passing through an existing DB role name
    return value


def _ensure_admin(current_user: dict) -> None:
    """Guard endpoints so only admins can perform user management."""

    role = (current_user or {}).get("role")
    # Backward-compat: support lowercase DB role names
    if not role or role.lower() != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can manage users",
        )


def _row_to_user_response(row) -> UserResponse:
    """
    Convert a joined users/roles/department/sections row into UserResponse.
    Row shape: id, username, email, first_name, last_name, department_id, section_id,
               department_name, section_name, is_active, created_at, role_name
    """
    return UserResponse(
        id=str(row[0]),
        username=row[1],
        email=row[2],
        first_name=row[3],
        last_name=row[4],
        department=row[7] or "",
        position="",
        department_id=row[5],
        section_id=str(row[6]) if row[6] else None,
        department_name=row[7] or "",
        section_name=row[8] or "",
        role=row[11],
        central_id="sentinel-local",
        created_at=row[10],
        raw_user={},
    )


@router.get("", response_model=List[UserListItem])
def list_users(current_user: dict = Depends(get_current_user)) -> List[UserListItem]:
    """
    List all SentinelOps users with their primary role.

    Restricted to admin users.
    """

    _ensure_admin(current_user)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    u.id,
                    u.username,
                    u.email,
                    u.first_name,
                    u.last_name,
                    u.department_id,
                    u.section_id,
                    d.department_name,
                    s.section_name,
                    u.is_active,
                    u.created_at,
                    COALESCE(MAX(r.name), 'user') AS role_name
                FROM users u
                LEFT JOIN user_roles ur ON ur.user_id = u.id
                LEFT JOIN roles r ON r.id = ur.role_id
                LEFT JOIN department d ON d.id = u.department_id
                LEFT JOIN sections s ON s.id = u.section_id
                GROUP BY
                    u.id, u.username, u.email, u.first_name, u.last_name,
                    u.department_id, u.section_id, d.department_name, s.section_name,
                    u.is_active, u.created_at
                ORDER BY u.created_at DESC
                """
            )
            rows = cur.fetchall()

    return [
        UserListItem(
            id=str(row[0]),
            username=row[1],
            email=row[2],
            first_name=row[3],
            last_name=row[4],
            department_id=row[5],
            section_id=str(row[6]) if row[6] else None,
            department_name=row[7] or "",
            section_name=row[8] or "",
            is_active=row[9],
            created_at=row[10],
            role=row[11],
        )
        for row in rows
    ]


@router.get("/by-section", response_model=List[UserListItem])
def list_users_by_section(
    section_id: Optional[str] = Query(None, description="Filter by section (required for managers)"),
    current_user: dict = Depends(get_current_user),
) -> List[UserListItem]:
    """
    List users in a section for shift assignment.
    Managers: restricted to their section (section_id defaults to their section).
    Admins: pass section_id to filter.
    """
    eff_section = section_id if section_id else str(current_user.get("section_id") or "")
    if not eff_section:
        raise HTTPException(status_code=400, detail="section_id required (or user must have section assigned)")
    if not is_admin(current_user) and eff_section != str(current_user.get("section_id")):
        raise HTTPException(status_code=403, detail="Access denied to this section")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.id, u.username, u.email, u.first_name, u.last_name,
                    u.department_id, u.section_id, d.department_name, s.section_name,
                    u.is_active, u.created_at, COALESCE(MAX(r.name), 'user') AS role_name
                FROM users u
                LEFT JOIN user_roles ur ON ur.user_id = u.id
                LEFT JOIN roles r ON r.id = ur.role_id
                LEFT JOIN department d ON d.id = u.department_id
                LEFT JOIN sections s ON s.id = u.section_id
                WHERE u.section_id = %s AND u.is_active = TRUE
                GROUP BY u.id, u.username, u.email, u.first_name, u.last_name,
                    u.department_id, u.section_id, d.department_name, s.section_name,
                    u.is_active, u.created_at
                ORDER BY u.first_name, u.last_name
                """,
                (eff_section,),
            )
            rows = cur.fetchall()

    return [
        UserListItem(
            id=str(row[0]), username=row[1], email=row[2], first_name=row[3], last_name=row[4],
            department_id=row[5], section_id=str(row[6]) if row[6] else None,
            department_name=row[7] or "", section_name=row[8] or "",
            is_active=row[9], created_at=row[10], role=row[11],
        )
        for row in rows
    ]


@router.get("/{user_id}", response_model=UserResponse)
def get_user(
    user_id: UUID, current_user: dict = Depends(get_current_user)
) -> UserResponse:
    """
    Get a single user by ID.

    Restricted to admin users.
    """

    _ensure_admin(current_user)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    u.id, u.username, u.email, u.first_name, u.last_name,
                    u.department_id, u.section_id,
                    d.department_name, s.section_name,
                    u.is_active, u.created_at,
                    COALESCE(r.name, 'operator') AS role_name
                FROM users u
                LEFT JOIN user_roles ur ON ur.user_id = u.id
                LEFT JOIN roles r ON r.id = ur.role_id
                LEFT JOIN department d ON d.id = u.department_id
                LEFT JOIN sections s ON s.id = u.section_id
                WHERE u.id = %s
                LIMIT 1
                """,
                (str(user_id),),
            )
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return _row_to_user_response(row)


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate, current_user: dict = Depends(get_current_user)
) -> UserResponse:
    """
    Create a new SentinelOps user and assign a primary role.

    - Password is hashed using the same mechanism as authentication
    - Role name is normalized (admin/manager/user) → DB role (admin/manager/user)
    - If username or email already exists, a 400 is returned
    """

    _ensure_admin(current_user)

    normalized_role = _normalize_role_name(payload.role)

    with get_connection() as conn:
        with conn.cursor() as cur:
            # Enforce uniqueness for username/email
            cur.execute(
                "SELECT 1 FROM users WHERE username = %s OR email = %s",
                (payload.username, payload.email),
            )
            if cur.fetchone():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Username or email already exists",
                )

            # Resolve role ID
            cur.execute(
                "SELECT id FROM roles WHERE name = %s",
                (normalized_role,),
            )
            role_row = cur.fetchone()
            if not role_row:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Role '{normalized_role}' does not exist",
                )
            role_id = role_row[0]

            # Create user
            password_hash = hash_password(payload.password)
            cur.execute(
                """
                INSERT INTO users (
                    username,
                    email,
                    password_hash,
                    first_name,
                    last_name,
                    department_id,
                    section_id,
                    is_active
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
                RETURNING id, created_at
                """,
                (
                    payload.username,
                    payload.email,
                    password_hash,
                    payload.first_name,
                    payload.last_name,
                    payload.department_id,
                    payload.section_id,
                ),
            )
            user_row = cur.fetchone()
            user_id, created_at = user_row

            # Assign primary role
            cur.execute(
                """
                INSERT INTO user_roles (user_id, role_id, assigned_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (user_id, role_id) DO NOTHING
                """,
                (user_id, role_id),
            )

            conn.commit()

    log.info("Created user %s with role %s", payload.username, normalized_role)

    dept_name = ""
    section_name = ""
    if payload.department_id or payload.section_id:
        with get_connection() as c2:
            with c2.cursor() as ccur:
                if payload.department_id:
                    ccur.execute("SELECT department_name FROM department WHERE id = %s", (payload.department_id,))
                    dn = ccur.fetchone()
                    dept_name = dn[0] if dn else ""
                if payload.section_id:
                    ccur.execute("SELECT section_name FROM sections WHERE id = %s", (payload.section_id,))
                    sn = ccur.fetchone()
                    section_name = sn[0] if sn else ""
    return UserResponse(
        id=str(user_id),
        username=payload.username,
        email=payload.email,
        first_name=payload.first_name,
        last_name=payload.last_name,
        department=dept_name,
        position="",
        department_id=payload.department_id,
        section_id=payload.section_id,
        department_name=dept_name,
        section_name=section_name,
        role=normalized_role,
        central_id="sentinel-local",
        created_at=created_at,
        raw_user={},
    )


@router.patch("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: UUID,
    payload: UserUpdate,
    current_user: dict = Depends(get_current_user),
) -> UserResponse:
    """
    Update an existing user:

    - Profile fields (name, department, position, email, username)
    - Activation status (is_active)
    - Primary role (admin/manager/user)
    - Optional password reset
    """

    _ensure_admin(current_user)

    updates = []
    params = []

    if payload.username is not None:
        updates.append("username = %s")
        params.append(payload.username)
    if payload.email is not None:
        updates.append("email = %s")
        params.append(payload.email)
    if payload.first_name is not None:
        updates.append("first_name = %s")
        params.append(payload.first_name)
    if payload.last_name is not None:
        updates.append("last_name = %s")
        params.append(payload.last_name)
    if payload.department_id is not None:
        updates.append("department_id = %s")
        params.append(payload.department_id)
    if payload.section_id is not None:
        updates.append("section_id = %s")
        params.append(payload.section_id)
    if payload.is_active is not None:
        updates.append("is_active = %s")
        params.append(payload.is_active)
    if payload.password is not None:
        updates.append("password_hash = %s")
        params.append(hash_password(payload.password))

    normalized_role = None
    if payload.role is not None:
        normalized_role = _normalize_role_name(payload.role)

    if not updates and normalized_role is None:
        allowed = (
            "username, email, first_name, last_name, department_id, "
            "section_id, is_active, password, role"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No fields provided for update. Provide at least one of: {allowed}",
        )

    with get_connection() as conn:
        with conn.cursor() as cur:
            # Apply basic field updates
            if updates:
                set_clause = ", ".join(updates)
                params_with_id = params + [str(user_id)]
                cur.execute(
                    f"UPDATE users SET {set_clause}, updated_at = NOW() WHERE id = %s",
                    params_with_id,
                )
                if cur.rowcount == 0:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
                    )

            # Handle role change, if requested
            if normalized_role is not None:
                cur.execute(
                    "SELECT id FROM roles WHERE name = %s",
                    (normalized_role,),
                )
                role_row = cur.fetchone()
                if not role_row:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Role '{normalized_role}' does not exist",
                    )
                role_id = role_row[0]

                # Clear existing roles and assign the new primary role
                cur.execute(
                    "DELETE FROM user_roles WHERE user_id = %s",
                    (str(user_id),),
                )
                cur.execute(
                    """
                    INSERT INTO user_roles (user_id, role_id, assigned_at)
                    VALUES (%s, %s, NOW())
                    """,
                    (str(user_id), role_id),
                )

            conn.commit()

            # Re-fetch updated row for response
            cur.execute(
                """
                SELECT
                    u.id, u.username, u.email, u.first_name, u.last_name,
                    u.department_id, u.section_id,
                    d.department_name, s.section_name,
                    u.is_active, u.created_at,
                    COALESCE(r.name, 'user') AS role_name
                FROM users u
                LEFT JOIN user_roles ur ON ur.user_id = u.id
                LEFT JOIN roles r ON r.id = ur.role_id
                LEFT JOIN department d ON d.id = u.department_id
                LEFT JOIN sections s ON s.id = u.section_id
                WHERE u.id = %s
                LIMIT 1
                """,
                (str(user_id),),
            )
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return _row_to_user_response(row)

