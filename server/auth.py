"""Agent enrollment/token (§3.7) and manager login auth. Stdlib crypto only."""
from __future__ import annotations

import os
import sys
import hashlib
import secrets

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared import contracts as C  # noqa: E402
from server import db  # noqa: E402

# ---- agent enrollment (§3.7) ---------------------------------------------- #


def issue_enrollment_code(conn, machine_id: str | None = None, label: str = "") -> str:
    """A one-time code. machine_id=None => binds to whichever host enrolls
    (self-serve install); pass a machine_id to pre-bind a specific machine."""
    code = secrets.token_hex(8)
    conn.execute(
        "INSERT INTO enrollment_code(code, machine_id, label, created_ts) VALUES (?,?,?,?)",
        (code, machine_id, label, db.now_iso()),
    )
    conn.commit()
    return code


def enroll(conn, code: str, hostname: str = "") -> str | None:
    row = conn.execute(
        "SELECT * FROM enrollment_code WHERE code=? AND used=0", (code,)
    ).fetchone()
    if not row:
        return None
    # use the code's pre-bound machine if set, else the enrolling host
    machine_id = C.normalize_machine_id(row["machine_id"] or hostname or "")
    if not machine_id:
        return None
    mfk = db.resolve_machine(conn, machine_id, auto_provision=True, hostname=hostname)
    token = C.make_agent_token(machine_id)
    conn.execute("UPDATE machine SET token=?, enrolled_ts=?, revoked=0 WHERE id=?",
                 (token, db.now_iso(), mfk))
    conn.execute("UPDATE enrollment_code SET used=1 WHERE code=?", (code,))
    conn.commit()
    return token


def check_enroll_password(conn, pw: str) -> bool:
    expected = db.get_setting(conn, "enroll_password", "") or ""
    return bool(pw) and secrets.compare_digest(pw, expected)


def verify_agent_token(conn, token: str) -> int | None:
    if not token:
        return None
    row = conn.execute(
        "SELECT id FROM machine WHERE token=? AND revoked=0", (token,)
    ).fetchone()
    return row["id"] if row else None


def revoke_machine(conn, machine_id: str) -> None:
    conn.execute("UPDATE machine SET revoked=1 WHERE machine_id=?", (machine_id,))
    conn.commit()


# ---- manager login -------------------------------------------------------- #


def _hash(pw: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000).hex()


def create_manager(conn, username: str, password: str, role: str = "manager",
                   display_name: str = "") -> int:
    salt = secrets.token_bytes(16)
    cur = conn.execute(
        "INSERT INTO manager(username, pw_hash, pw_salt, role, display_name, created_ts) "
        "VALUES (?,?,?,?,?,?)",
        (username, _hash(password, salt), salt.hex(), role,
         display_name or username, db.now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def count_managers(conn) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM manager").fetchone()["n"]


def create_first_admin(conn, username: str, password: str, display_name: str = ""):
    """Create the very first admin — only when no manager exists yet (first-run bootstrap).
    Returns the manager row, or None if the org already has a manager (re-bootstrap guard).
    """
    if count_managers(conn) != 0:
        return None
    mid = create_manager(conn, username, password, role="admin", display_name=display_name)
    return conn.execute("SELECT * FROM manager WHERE id=?", (mid,)).fetchone()


def set_manager_scope(conn, manager_id: int, user_ids) -> None:
    """Replace a manager's direct-report scope with exactly `user_ids` (admin action)."""
    conn.execute("DELETE FROM manager_scope WHERE manager_fk=?", (manager_id,))
    for uid in user_ids:
        conn.execute(
            "INSERT OR IGNORE INTO manager_scope(manager_fk, user_fk) VALUES (?,?)",
            (manager_id, int(uid)),
        )
    conn.commit()


def verify_login(conn, username: str, password: str):
    row = conn.execute("SELECT * FROM manager WHERE username=?", (username,)).fetchone()
    if not row:
        return None
    if secrets.compare_digest(_hash(password, bytes.fromhex(row["pw_salt"])), row["pw_hash"]):
        return row
    return None


def visible_user_ids(conn, manager_row) -> list[int] | None:
    """None means 'all' (admin); else the manager's direct-report user ids (§3.10 RBAC)."""
    if manager_row["role"] == "admin":
        return None
    rows = conn.execute(
        "SELECT user_fk FROM manager_scope WHERE manager_fk=?", (manager_row["id"],)
    ).fetchall()
    return [r["user_fk"] for r in rows]
