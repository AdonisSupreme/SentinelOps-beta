from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.schemas import UserResponse
from app.auth.service import get_current_user
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
      - manager    → supervisor
      - user       → operator
    """

    if not role:
        raise ValueError("Role is required")

    value = role.strip().lower()
    if value in {"admin", "administrator"}:
        return "admin"
    if value in {"manager", "supervisor"}:
        return "supervisor"
    if value in {"user", "operator", "participant"}:
        return "operator"

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
    Convert a joined users/roles row into a UserResponse-compatible dict.

    Expected row shape (columns):
      0: id
      1: username
      2: email
      3: first_name
      4: last_name
      5: department
      6: position
      7: is_active
      8: created_at
      9: role_name
    """

    return UserResponse(
        id=str(row[0]),
        username=row[1],
        email=row[2],
        first_name=row[3],
        last_name=row[4],
        department=row[5] or "",
        position=row[6] or "",
        role=row[9],
        central_id="sentinel-local",
        created_at=row[8],
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
                    '' AS department,
                    '' AS position,
                    u.is_active,
                    u.created_at,
                    COALESCE(MAX(r.name), 'operator') AS role_name
                FROM users u
                LEFT JOIN user_roles ur ON ur.user_id = u.id
                LEFT JOIN roles r ON r.id = ur.role_id
                GROUP BY
                    u.id,
                    u.username,
                    u.email,
                    u.first_name,
                    u.last_name,
                    u.is_active,
                    u.created_at
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
            department=row[5],
            position=row[6],
            is_active=row[7],
            created_at=row[8],
            role=row[9],
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
                    u.id,
                    u.username,
                    u.email,
                    u.first_name,
                    u.last_name,
                    '' AS department,
                    '' AS position,
                    u.is_active,
                    u.created_at,
                    COALESCE(r.name, 'operator') AS role_name
                FROM users u
                LEFT JOIN user_roles ur ON ur.user_id = u.id
                LEFT JOIN roles r ON r.id = ur.role_id
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
    - Role name is normalized (admin/manager/user) → DB role (admin/supervisor/operator)
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
                    department,
                    position,
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
                    payload.department or "",
                    payload.position or "",
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

    return UserResponse(
        id=str(user_id),
        username=payload.username,
        email=payload.email,
        first_name=payload.first_name,
        last_name=payload.last_name,
        department=payload.department or "",
        position=payload.position or "",
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
    - Primary role (admin/manager/user → admin/supervisor/operator)
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
    if payload.department is not None:
        updates.append("department = %s")
        params.append(payload.department)
    if payload.position is not None:
        updates.append("position = %s")
        params.append(payload.position)
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields provided for update",
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
                    u.id,
                    u.username,
                    u.email,
                    u.first_name,
                    u.last_name,
                    '' AS department,
                    '' AS position,
                    u.is_active,
                    u.created_at,
                    COALESCE(r.name, 'operator') AS role_name
                FROM users u
                LEFT JOIN user_roles ur ON ur.user_id = u.id
                LEFT JOIN roles r ON r.id = ur.role_id
                WHERE u.id = %s
                LIMIT 1
                """,
                (str(user_id),),
            )
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return _row_to_user_response(row)

