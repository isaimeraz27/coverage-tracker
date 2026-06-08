"""Schema migrations: fresh DBs reach the latest version, the run is idempotent, and a
DB that predates the migrations table converges (the upgrade-from-old path)."""
import os
import sys
import sqlite3
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from server import db, migrations  # noqa: E402


def _cols(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


class TestMigrations(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

    def tearDown(self):
        os.unlink(self.path)

    def test_fresh_db_reaches_latest(self):
        conn = db.connect(self.path)
        db.init_db(conn)
        self.assertEqual(migrations.current_version(conn), migrations.LATEST)
        self.assertTrue({"target_score", "calibrated_ts"} <= _cols(conn, "role"))
        self.assertTrue({"display_name", "created_ts"} <= _cols(conn, "manager"))

    def test_idempotent(self):
        conn = db.connect(self.path)
        db.init_db(conn)
        self.assertEqual(migrations.apply_pending(conn), [])  # already at latest → no-op
        # running init_db again must not raise or duplicate
        db.init_db(conn)
        self.assertEqual(migrations.current_version(conn), migrations.LATEST)

    def test_upgrade_from_old_db(self):
        # A DB created before schema_migrations existed, already carrying target_score but
        # missing calibrated_ts — the guard must add only what's missing, no duplicate/error.
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE role (id INTEGER PRIMARY KEY, name TEXT UNIQUE, target_score REAL)")
        conn.execute("CREATE TABLE manager (id INTEGER PRIMARY KEY, username TEXT, pw_hash TEXT, pw_salt TEXT, role TEXT)")
        conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_ts TEXT NOT NULL)")
        conn.commit()
        applied = migrations.apply_pending(conn)
        self.assertIn(1, applied)
        rcols = list(_cols(conn, "role"))
        self.assertIn("calibrated_ts", rcols)
        self.assertEqual([c for c in rcols].count("target_score"), 1)  # not duplicated
        self.assertTrue({"display_name", "created_ts"} <= _cols(conn, "manager"))
        self.assertEqual(migrations.current_version(conn), migrations.LATEST)


if __name__ == "__main__":
    unittest.main()
