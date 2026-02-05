import sys
from pathlib import Path

# Make sure project root is on sys.path so running
# `python scripts/create_user.py` works (it sets sys.path[0] to scripts/)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rich.console import Console
from app.core.logging import get_logger
from app.db.database import get_connection
from app.core.security import hash_password

log = get_logger("user-bootstrap")
console = Console()

USERNAME = "zmalunga"
EMAIL = "zmalunga@afcholdings.co.zw"
FIRST_NAME = "Zviko"
LAST_NAME = "Malunga"

PLAINTEXT_PASSWORD = "Sentinel@123"  # ← THIS IS THE PASSWORD

ROLE_NAME = "user"


def main():
    log.info("👤 Bootstrapping system admin user")

    # Hash password at runtime so import-time failures (e.g. broken bcrypt backend)
    # don't occur when merely importing the module.
    # Diagnostic: inspect and log bcrypt/passlib info to help debug backend issues.
    try:
        try:
            import bcrypt as _bcrypt_mod
            # try common version/about attributes
            ver = getattr(_bcrypt_mod, "__version__", None) or getattr(_bcrypt_mod, "__about__", None)
            if not ver:
                ver = getattr(getattr(_bcrypt_mod, "_bcrypt", None), "__version__", None)
            log.info("bcrypt detected: %s", ver or "<unknown>")
            # helpful debug flags
            log.debug("bcrypt module attributes: __version__=%s, has__about__=%s",
                      getattr(_bcrypt_mod, "__version__", None), hasattr(_bcrypt_mod, "__about__"))
        except Exception as _err:  # pragma: no cover - diagnostic only
            log.debug("bcrypt inspection failed: %s", _err)

        hashed_password = hash_password(PLAINTEXT_PASSWORD)
    except Exception:
        log.exception("💥 Failed to hash password. Ensure a working bcrypt backend is installed (e.g. `pip install bcrypt-cffi`).")
        sys.exit(1)

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:

                # 1. Create user
                cur.execute(
                    """
                    INSERT INTO users (
                        username,
                        email,
                        first_name,
                        last_name,
                        password_hash,
                        is_active,
                        is_locked,
                        is_flagged,
                        created_by
                    )
                    VALUES (%s, %s, %s, %s, %s, TRUE, FALSE, FALSE, NULL)
                    ON CONFLICT (username) DO NOTHING
                    RETURNING id
                    """,
                    (
                        USERNAME,
                        EMAIL,
                        FIRST_NAME,
                        LAST_NAME,
                        hashed_password,
                    ),
                )

                row = cur.fetchone()

                if row is None:
                    # User already exists → fetch ID
                    cur.execute(
                        "SELECT id FROM users WHERE username = %s",
                        (USERNAME,),
                    )
                    user_id = cur.fetchone()[0]
                    log.warning("⚠️ User already exists, reusing existing record")
                else:
                    user_id = row[0]
                    log.info("✅ User record created")

                # 2. Fetch admin role
                cur.execute(
                    "SELECT id FROM roles WHERE name = %s",
                    (ROLE_NAME,),
                )
                role_row = cur.fetchone()

                if not role_row:
                    raise RuntimeError("Role does not exist")

                role_id = role_row[0]

                # 3. Assign role to user
                cur.execute(
                    """
                    INSERT INTO user_roles (user_id, role_id, assigned_by)
                    VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (user_id, role_id, user_id),
                )

                # 4. Set created_by = self (optional but clean)
                cur.execute(
                    """
                    UPDATE users
                    SET created_by = %s
                    WHERE id = %s AND created_by IS NULL
                    """,
                    (user_id, user_id),
                )

                conn.commit()

        console.print(
            f"[bold green]✅ System {ROLE_NAME} ready[/bold green]\n"
            f"[cyan]Username:[/cyan] {USERNAME}\n"
            f"[cyan]Password:[/cyan] {PLAINTEXT_PASSWORD}\n"
            f"[cyan]Role:[/cyan] {ROLE_NAME}"
        )

    except Exception:
        log.exception("💥 Failed to bootstrap system user")
        sys.exit(1)


if __name__ == "__main__":
    main()
