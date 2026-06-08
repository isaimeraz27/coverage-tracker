"""Numbered, idempotent schema migrations layered on the v0 baseline.

The baseline schema (db.SCHEMA, applied via CREATE TABLE IF NOT EXISTS) is "v0".
These migrations carry it forward additively. Everything here is nullable / additive
so a partially-migrated or pre-migrations DB converges safely.

This module takes a raw sqlite3 connection and MUST NOT import server.db (would be a
cycle: db.init_db calls us). 3.9-safe: `from __future__ import annotations` so the
`list[...]` annotations below are strings, never evaluated at runtime.
"""
from __future__ import annotations

import datetime as dt

# (version, name, statements). Each statement runs only if its guard passes; ADD COLUMN
# statements are skipped when the column already exists (SQLite has no ADD COLUMN IF NOT
# EXISTS), so a DB that predates the migrations table but already has the column converges.
MIGRATIONS = [
    (1, "role_target_calibration", [
        ("role", "target_score", "ALTER TABLE role ADD COLUMN target_score REAL"),
        ("role", "calibrated_ts", "ALTER TABLE role ADD COLUMN calibrated_ts TEXT"),
    ]),
    (2, "manager_profile", [
        ("manager", "display_name", "ALTER TABLE manager ADD COLUMN display_name TEXT"),
        ("manager", "created_ts", "ALTER TABLE manager ADD COLUMN created_ts TEXT"),
    ]),
    # v3 — admin-editable taxonomy rules (app/domain/url_path/title -> sub_category).
    # guard_col is None for CREATE TABLE/INDEX IF NOT EXISTS so the step always runs but
    # is idempotent. The actual app/domain->category mapping moves from the Python seeds
    # (now fallback-only) into this table so it's editable and applies retroactively.
    (3, "taxonomy_rules", [
        (None, None, """
        CREATE TABLE IF NOT EXISTS taxonomy_rule (
          id           INTEGER PRIMARY KEY,
          match_type   TEXT NOT NULL CHECK (match_type IN ('app','domain','url_path','title')),
          pattern      TEXT NOT NULL,          -- app: exact lc name; domain: host/suffix;
                                               -- url_path: 'host/glob/path'; title: substring lc
          sub_category TEXT NOT NULL,          -- target fine category (soft FK to category)
          is_meeting   INTEGER NOT NULL DEFAULT 0,
          priority     INTEGER NOT NULL DEFAULT 100,   -- lower wins
          enabled      INTEGER NOT NULL DEFAULT 1,
          notes        TEXT,
          created_ts   TEXT,
          updated_ts   TEXT,
          UNIQUE (match_type, pattern)
        )"""),
        (None, None, "CREATE INDEX IF NOT EXISTS ix_taxrule_enabled ON taxonomy_rule(enabled, priority)"),
    ]),
    # v4 — admin-defined workflow templates (a "new-business quote" = a set/sequence of
    # categories within a time window). Phase B / workflow-task detection.
    (4, "workflow_templates", [
        (None, None, """
        CREATE TABLE IF NOT EXISTS workflow_template (
          id                  INTEGER PRIMARY KEY,
          name                TEXT NOT NULL UNIQUE,
          match_mode          TEXT NOT NULL DEFAULT 'set_within_window'
                              CHECK (match_mode IN ('set_within_window','sequence')),
          window_s            INTEGER NOT NULL DEFAULT 1800,
          expected_duration_s INTEGER,         -- coaching comparison only (no verdict)
          enabled             INTEGER NOT NULL DEFAULT 1,
          notes               TEXT
        )"""),
        (None, None, """
        CREATE TABLE IF NOT EXISTS workflow_template_step (
          template_fk  INTEGER NOT NULL REFERENCES workflow_template(id),
          sub_category TEXT NOT NULL,
          required     INTEGER NOT NULL DEFAULT 1,
          step_order   INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY (template_fk, sub_category)
        )"""),
    ]),
]
# NOTE: the `mode` / `first_run_complete` settings keys are not DDL — they are seeded as
# rows by db.SETTINGS_DEFAULTS in db.init_db, so they need no migration here.

LATEST = max((v for v, _, _ in MIGRATIONS), default=0)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _column_exists(conn, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    # rows may be sqlite3.Row or tuple depending on row_factory; index 1 is the name
    return any((r["name"] if hasattr(r, "keys") else r[1]) == col for r in rows)


def current_version(conn) -> int:
    row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
    v = (row["v"] if hasattr(row, "keys") else row[0]) if row else None
    return int(v) if v is not None else 0


def apply_pending(conn) -> list:
    """Run every migration with version > current_version, one transaction each.

    Idempotent: re-running is a no-op; ADD COLUMN steps whose column already exists are
    skipped, so a DB created before schema_migrations existed (but already carrying some
    columns via an old baseline) upgrades cleanly.
    """
    cur = current_version(conn)
    applied = []
    for version, name, steps in sorted(MIGRATIONS, key=lambda m: m[0]):
        if version <= cur:
            continue
        for guard_table, guard_col, sql in steps:
            if guard_col and _column_exists(conn, guard_table, guard_col):
                continue  # already present — converge without erroring
            conn.execute(sql)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_ts) VALUES (?,?)",
            (version, _now()),
        )
        conn.commit()
        applied.append(version)
    return applied


def migrate(conn) -> list:
    return apply_pending(conn)
