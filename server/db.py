"""SQLite schema + access layer (§3.1 identity, §3.3 categories, §3.6 retention).

SQLite is sufficient for <=10 users (§ data-model). All foreign keys are INTEGER
surrogate keys; the agent's wire strings (machine_id, username) resolve here.
"""
from __future__ import annotations

import os
import sqlite3
import datetime as dt
from typing import Optional

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared import contracts as C  # noqa: E402

DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "tracker.db")

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS machine (
  id            INTEGER PRIMARY KEY,
  machine_id    TEXT UNIQUE NOT NULL,          -- wire id (lowercased hostname/guid)
  hostname      TEXT,
  token         TEXT,                          -- eat_<machine_id>_<rand>
  revoked       INTEGER NOT NULL DEFAULT 0,
  enrolled_ts   TEXT,
  last_seen_ts  TEXT,
  clock_skew_s  REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS role (
  id        INTEGER PRIMARY KEY,
  name      TEXT UNIQUE NOT NULL
);

-- §3.3 the on_task_set: fine categories that count as "doing their job"
CREATE TABLE IF NOT EXISTS role_on_task (
  role_fk       INTEGER NOT NULL REFERENCES role(id),
  sub_category  TEXT NOT NULL,
  PRIMARY KEY (role_fk, sub_category)
);

CREATE TABLE IF NOT EXISTS app_user (
  id           INTEGER PRIMARY KEY,
  machine_fk   INTEGER NOT NULL REFERENCES machine(id),
  username     TEXT NOT NULL,
  display_name TEXT,
  role_fk      INTEGER REFERENCES role(id),
  tz           TEXT DEFAULT 'UTC',
  UNIQUE (machine_fk, username)                 -- §3.1 keyed per (machine, user)
);

-- §3.3 two-level category model (replaces the four-value CHECK)
CREATE TABLE IF NOT EXISTS category (
  sub_category TEXT PRIMARY KEY,
  coarse_class TEXT NOT NULL CHECK (coarse_class IN ('productive','neutral','distracting','idle'))
);

CREATE TABLE IF NOT EXISTS activity_event (
  id            INTEGER PRIMARY KEY,
  machine_fk    INTEGER NOT NULL REFERENCES machine(id),
  user_fk       INTEGER NOT NULL REFERENCES app_user(id),
  client_event_id TEXT UNIQUE NOT NULL,         -- §3.7 idempotency
  ts            TEXT NOT NULL,                   -- agent local
  ts_norm       TEXT NOT NULL,                   -- skew-corrected (§3.1)
  app           TEXT,
  window_title  TEXT,                            -- nulled at titles_urls retention
  domain        TEXT,
  url           TEXT,                            -- only if role-enabled
  sub_category  TEXT NOT NULL DEFAULT 'uncategorized',
  category_code TEXT NOT NULL DEFAULT 'neutral',
  state         TEXT NOT NULL DEFAULT 'active',
  active_ms     INTEGER NOT NULL DEFAULT 0,
  idle_ms       INTEGER NOT NULL DEFAULT 0,      -- §3.4 explicit
  is_meeting    INTEGER NOT NULL DEFAULT 0,      -- §3.5
  key_count     INTEGER NOT NULL DEFAULT 0,
  mouse_count   INTEGER NOT NULL DEFAULT 0,      -- clicks
  mouse_distance_px INTEGER NOT NULL DEFAULT 0,  -- cursor travel (engagement signal)
  suspect_time  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_act_user_ts ON activity_event(user_fk, ts_norm);

CREATE TABLE IF NOT EXISTS attendance_event (
  id            INTEGER PRIMARY KEY,
  machine_fk    INTEGER NOT NULL REFERENCES machine(id),
  user_fk       INTEGER NOT NULL REFERENCES app_user(id),
  client_event_id TEXT UNIQUE NOT NULL,
  ts            TEXT NOT NULL,
  ts_norm       TEXT NOT NULL,
  subtype       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS screenshot (
  id            INTEGER PRIMARY KEY,
  machine_fk    INTEGER NOT NULL REFERENCES machine(id),
  user_fk       INTEGER NOT NULL REFERENCES app_user(id),
  image_id      TEXT UNIQUE NOT NULL,
  taken_ts      TEXT NOT NULL,
  monitor       INTEGER, width INTEGER, height INTEGER,
  phash         TEXT,
  redacted      INTEGER NOT NULL DEFAULT 1,
  stored_path   TEXT,
  encrypted     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS agent_health (
  id            INTEGER PRIMARY KEY,
  machine_fk    INTEGER NOT NULL REFERENCES machine(id),
  ts            TEXT NOT NULL,
  agent_version TEXT, cpu_pct REAL, buffer_depth INTEGER, clock_skew_s REAL
);

-- §3.6 derived daily aggregate (kept 13 months; no titles/urls/screenshots)
CREATE TABLE IF NOT EXISTS daily_agg (
  id            INTEGER PRIMARY KEY,
  user_fk       INTEGER NOT NULL REFERENCES app_user(id),
  day           TEXT NOT NULL,
  present_s     INTEGER, active_s INTEGER, on_task_s INTEGER, meeting_s INTEGER,
  distract_s    INTEGER, idle_short_s INTEGER, idle_long_s INTEGER,
  adherence     REAL, distract_ratio REAL, focus_quality REAL, score REAL,
  engagement    REAL,
  data_completeness REAL,
  top_json      TEXT,
  computed_ts   TEXT,
  UNIQUE (user_fk, day)
);

CREATE TABLE IF NOT EXISTS manager (
  id            INTEGER PRIMARY KEY,
  username      TEXT UNIQUE NOT NULL,
  pw_hash       TEXT NOT NULL,
  pw_salt       TEXT NOT NULL,
  role          TEXT NOT NULL DEFAULT 'manager'  -- 'admin' | 'manager'
);

CREATE TABLE IF NOT EXISTS manager_scope (   -- direct reports for non-admins
  manager_fk    INTEGER NOT NULL REFERENCES manager(id),
  user_fk       INTEGER NOT NULL REFERENCES app_user(id),
  PRIMARY KEY (manager_fk, user_fk)
);

CREATE TABLE IF NOT EXISTS audit_log (      -- §3.6/§3.10 who viewed whom
  id            INTEGER PRIMARY KEY,
  ts            TEXT NOT NULL,
  manager_fk    INTEGER,
  action        TEXT NOT NULL,
  target_user_fk INTEGER,
  detail        TEXT
);

CREATE TABLE IF NOT EXISTS enrollment_code (
  code          TEXT PRIMARY KEY,
  machine_id    TEXT,                          -- NULL = bind to whatever host enrolls (self-serve)
  label         TEXT,                          -- who it was issued to (name/email)
  used          INTEGER NOT NULL DEFAULT 0,
  created_ts    TEXT
);

-- org-wide configuration the master dashboard controls (work hours, enroll pw, etc.)
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT
);

-- numbered schema migrations layered on this v0 baseline (server/migrations.py)
CREATE TABLE IF NOT EXISTS schema_migrations (
  version    INTEGER PRIMARY KEY,
  applied_ts TEXT NOT NULL
);
"""

# defaults the dashboard can override (§ work hours, self-serve enrollment)
SETTINGS_DEFAULTS = {
    "work_start": "8",          # hour, local agent time
    "work_end": "18",
    "work_days": "0,1,2,3,4",   # 0=Mon … 6=Sun  (Python weekday())
    "poll_ms": "5000",
    "full_url": "0",            # capture full URLs (deep app context); off by default
    "enroll_password": "coverage-setup",   # employees type this on the setup page
    "org_name": "Coverage Insurance",
    "mode": "coaching",                    # 'coaching' (default) | 'evaluative' — see present_insight()
    "first_run_complete": "0",             # "1" once the first admin + org config is saved
}


def connect(path: str = DEFAULT_DB, check_same_thread: bool = True) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Generic, EDITABLE example taxonomy rules — NOT a vendor list. They mirror the old
# Python seeds so a fresh org behaves sensibly, plus one url_path example to show how an
# admin maps a workflow *stage* (e.g. a rater's rating screen) within one tool. Admins are
# expected to replace these with their own agency's stack via the Taxonomy admin screen.
# Format: (match_type, pattern, sub_category, is_meeting, priority, notes)
TAXONOMY_SEED = [
    ("app", "outlook", "work_comms", 0, 100, None),
    ("app", "slack", "work_comms", 0, 100, None),
    ("app", "teams", "work_comms", 0, 100, None),
    ("app", "excel", "office_docs", 0, 100, None),
    ("app", "winword", "office_docs", 0, 100, None),
    ("domain", "github.com", "code_review", 0, 80, None),
    ("domain", "youtube.com", "streaming", 0, 80, None),
    ("domain", "reddit.com", "social", 0, 80, None),
    # url_path example — most specific (lowest priority number). Edit me: point this at
    # your rating engine's quoting screen, e.g. 'app.ezlynx.com/quotes/*/rating'.
    ("url_path", "example-rater.com/quotes/*/rating", "rating", 0, 20,
     "EXAMPLE — replace host with your real rating engine"),
]


def seed_taxonomy(conn) -> None:
    """Insert the generic example rules (idempotent) and ensure each rule's sub_category
    exists in the category registry. Runs after migrations so taxonomy_rule exists."""
    for sub in {r[2] for r in TAXONOMY_SEED}:
        # default coarse: reuse the Python seed's class if known, else neutral
        coarse = C.CATEGORY_SEED.get(sub)
        conn.execute("INSERT OR IGNORE INTO category(sub_category, coarse_class) VALUES (?,?)",
                     (sub, (coarse.value if coarse else C.CoarseClass.NEUTRAL.value)))
    for mt, pat, sub, mtg, pri, notes in TAXONOMY_SEED:
        conn.execute(
            "INSERT OR IGNORE INTO taxonomy_rule"
            "(match_type, pattern, sub_category, is_meeting, priority, notes, created_ts, updated_ts) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (mt, pat, sub, mtg, pri, notes, now_iso(), now_iso()))
    conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # seed categories
    for sub, coarse in C.CATEGORY_SEED.items():
        conn.execute(
            "INSERT OR IGNORE INTO category(sub_category, coarse_class) VALUES (?,?)",
            (sub, coarse.value),
        )
    # seed settings defaults (never overwrite an admin-set value)
    for k, v in SETTINGS_DEFAULTS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?,?)", (k, v))
    conn.commit()
    # carry the baseline forward with numbered migrations (lazy import avoids a cycle:
    # migrations.py must not import server.db)
    from server import migrations  # noqa: E402
    migrations.apply_pending(conn)
    # taxonomy rules seed AFTER migrations (the taxonomy_rule table is created by v3)
    seed_taxonomy(conn)


def get_setting(conn, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else (SETTINGS_DEFAULTS.get(key, default))


def set_setting(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    conn.commit()


def all_settings(conn) -> dict:
    out = dict(SETTINGS_DEFAULTS)
    for r in conn.execute("SELECT key, value FROM settings").fetchall():
        out[r["key"]] = r["value"]
    return out


def work_hours(conn) -> dict:
    """The tracking window the agent must honor, as set by the dashboard."""
    return {
        "work_start": int(get_setting(conn, "work_start", "8")),
        "work_end": int(get_setting(conn, "work_end", "18")),
        "work_days": [int(x) for x in get_setting(conn, "work_days", "0,1,2,3,4").split(",") if x != ""],
        "poll_ms": int(get_setting(conn, "poll_ms", "5000")),
        "full_url": get_setting(conn, "full_url", "0") in ("1", "true", "True"),
    }


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


# ---- identity resolution (§3.1) ------------------------------------------- #


def resolve_machine(conn, machine_id: str, auto_provision: bool = False, hostname: str = "") -> Optional[int]:
    row = conn.execute("SELECT id FROM machine WHERE machine_id=?", (machine_id,)).fetchone()
    if row:
        return row["id"]
    if not auto_provision:
        return None
    cur = conn.execute(
        "INSERT INTO machine(machine_id, hostname, enrolled_ts) VALUES (?,?,?)",
        (machine_id, hostname or machine_id, now_iso()),
    )
    return cur.lastrowid


def resolve_user(conn, machine_fk: int, username: str, auto_provision: bool = False) -> Optional[int]:
    row = conn.execute(
        "SELECT id FROM app_user WHERE machine_fk=? AND username=?", (machine_fk, username)
    ).fetchone()
    if row:
        return row["id"]
    if not auto_provision:
        return None
    cur = conn.execute(
        "INSERT INTO app_user(machine_fk, username, display_name) VALUES (?,?,?)",
        (machine_fk, username, username),
    )
    return cur.lastrowid


def ensure_role(conn, name: str, on_task: list[str]) -> int:
    conn.execute("INSERT OR IGNORE INTO role(name) VALUES (?)", (name,))
    rid = conn.execute("SELECT id FROM role WHERE name=?", (name,)).fetchone()["id"]
    for sub in on_task:
        conn.execute("INSERT OR IGNORE INTO role_on_task(role_fk, sub_category) VALUES (?,?)", (rid, sub))
    conn.commit()
    return rid


def role_on_task_set(conn, role_fk: Optional[int]) -> set[str]:
    if role_fk is None:
        # no role assigned: treat all coarse-productive fine categories as on-task
        return {s for s, c in C.CATEGORY_SEED.items() if c == C.CoarseClass.PRODUCTIVE}
    rows = conn.execute("SELECT sub_category FROM role_on_task WHERE role_fk=?", (role_fk,)).fetchall()
    return {r["sub_category"] for r in rows}


def coarse_for(conn, sub: str) -> str:
    """Coarse class for a fine sub_category: the DB category registry is authoritative,
    falling back to the Python seed (C.coarse_of) for anything not yet registered."""
    row = conn.execute("SELECT coarse_class FROM category WHERE sub_category=?", (sub,)).fetchone()
    return row["coarse_class"] if row else C.coarse_of(sub).value


def taxonomy_rules(conn) -> list:
    """All enabled taxonomy rules, ordered for matching: priority asc, then by specificity
    (url_path > domain > app > title), then id. Returns plain dict rows."""
    spec = "CASE match_type WHEN 'url_path' THEN 0 WHEN 'domain' THEN 1 WHEN 'app' THEN 2 ELSE 3 END"
    rows = conn.execute(
        "SELECT id, match_type, pattern, sub_category, is_meeting FROM taxonomy_rule "
        f"WHERE enabled=1 ORDER BY priority ASC, {spec} ASC, id ASC").fetchall()
    return [dict(r) for r in rows]


def workflow_templates(conn) -> list:
    """Enabled workflow templates shaped for insights.workflows.detect_tasks:
    {name, match_mode, window_s, expected_duration_s, steps[], required{sub:bool}, order[]}."""
    out = []
    trows = conn.execute(
        "SELECT id, name, match_mode, window_s, expected_duration_s FROM workflow_template "
        "WHERE enabled=1 ORDER BY id").fetchall()
    for t in trows:
        steps = conn.execute(
            "SELECT sub_category, required, step_order FROM workflow_template_step "
            "WHERE template_fk=? ORDER BY step_order, sub_category", (t["id"],)).fetchall()
        out.append({
            "id": t["id"], "name": t["name"], "match_mode": t["match_mode"],
            "window_s": t["window_s"], "expected_duration_s": t["expected_duration_s"],
            "steps": [s["sub_category"] for s in steps],
            "required": {s["sub_category"]: bool(s["required"]) for s in steps},
            "order": [s["sub_category"] for s in steps],
        })
    return out


def ensure_workflow_template(conn, name, match_mode, window_s, steps,
                            expected_duration_s=None, notes=None) -> int:
    """Create/replace a workflow template + its steps. `steps` is a list of
    (sub_category, required, step_order). Returns the template id."""
    conn.execute(
        "INSERT INTO workflow_template(name, match_mode, window_s, expected_duration_s, notes) "
        "VALUES (?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
        "match_mode=excluded.match_mode, window_s=excluded.window_s, "
        "expected_duration_s=excluded.expected_duration_s, notes=excluded.notes",
        (name, match_mode, window_s, expected_duration_s, notes))
    tid = conn.execute("SELECT id FROM workflow_template WHERE name=?", (name,)).fetchone()["id"]
    conn.execute("DELETE FROM workflow_template_step WHERE template_fk=?", (tid,))
    for i, (sub, req, order) in enumerate(steps):
        conn.execute(
            "INSERT OR IGNORE INTO workflow_template_step"
            "(template_fk, sub_category, required, step_order) VALUES (?,?,?,?)",
            (tid, sub, 1 if req else 0, order if order is not None else i))
    conn.commit()
    return tid


# ---- §3.6 retention purge -------------------------------------------------- #


def purge_expired(conn, ref: Optional[dt.datetime] = None) -> dict:
    """Enforce the §3.6 retention schedule. Returns counts purged."""
    ref = ref or dt.datetime.now(dt.timezone.utc)

    def cutoff(days: int) -> str:
        return (ref - dt.timedelta(days=days)).isoformat()

    counts = {}
    # titles/urls nulled at 14d (event row survives to 45d)
    cur = conn.execute(
        "UPDATE activity_event SET window_title=NULL, url=NULL "
        "WHERE ts_norm < ? AND (window_title IS NOT NULL OR url IS NOT NULL)",
        (cutoff(C.RETENTION_DAYS["titles_urls"]),),
    )
    counts["titles_urls_nulled"] = cur.rowcount
    # raw events purged at 45d
    for tbl in ("activity_event", "attendance_event"):
        cur = conn.execute(f"DELETE FROM {tbl} WHERE ts_norm < ?", (cutoff(C.RETENTION_DAYS["raw_events"]),))
        counts[tbl] = cur.rowcount
    # screenshots 7d
    cur = conn.execute("DELETE FROM screenshot WHERE taken_ts < ?", (cutoff(C.RETENTION_DAYS["screenshots"]),))
    counts["screenshot"] = cur.rowcount
    # aggregates 13mo
    cur = conn.execute("DELETE FROM daily_agg WHERE day < ?", ((ref - dt.timedelta(days=C.RETENTION_DAYS["aggregates"])).date().isoformat(),))
    counts["daily_agg"] = cur.rowcount
    # audit 365d minimum
    cur = conn.execute("DELETE FROM audit_log WHERE ts < ?", (cutoff(C.RETENTION_DAYS["audit"]),))
    counts["audit_log"] = cur.rowcount
    conn.commit()
    return counts


def audit(conn, manager_fk, action, target_user_fk=None, detail=""):
    conn.execute(
        "INSERT INTO audit_log(ts, manager_fk, action, target_user_fk, detail) VALUES (?,?,?,?,?)",
        (now_iso(), manager_fk, action, target_user_fk, detail),
    )
    conn.commit()
