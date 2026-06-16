"""Tests for the §3.6 retention purge scheduler hook.

Verifies that run_purge_once():
  - deletes an activity_event whose ts_norm is beyond the raw_events retention window,
  - keeps a fresh event,
  - returns a counts dict,
  - swallows a failed purge (returns {}) so the scheduler never crashes the server.
"""
from __future__ import annotations

import os
import sys
import datetime as dt
import tempfile
import threading
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import api, db          # noqa: E402
import shared.contracts as C        # noqa: E402


def _make_server_stub(path: str):
    """Minimal stand-in with the two attributes run_purge_once needs."""
    conn = db.connect(path, check_same_thread=False)
    db.init_db(conn)
    return types.SimpleNamespace(conn=conn, lock=threading.Lock())


def _seed_events(conn, old_ts: str, fresh_ts: str):
    """Insert machine + user rows (required FKs) then two activity_events."""
    conn.execute(
        "INSERT OR IGNORE INTO machine(machine_id, hostname) VALUES ('test-machine', 'testhost')"
    )
    mfk = conn.execute(
        "SELECT id FROM machine WHERE machine_id='test-machine'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT OR IGNORE INTO app_user(machine_fk, username) VALUES (?, 'testuser')", (mfk,)
    )
    ufk = conn.execute(
        "SELECT id FROM app_user WHERE machine_fk=? AND username='testuser'", (mfk,)
    ).fetchone()["id"]

    for ts, cid in [(old_ts, "cid-old-001"), (fresh_ts, "cid-fresh-001")]:
        conn.execute(
            "INSERT OR IGNORE INTO activity_event"
            "(machine_fk, user_fk, client_event_id, ts, ts_norm)"
            " VALUES (?, ?, ?, ?, ?)",
            (mfk, ufk, cid, ts, ts),
        )
    conn.commit()
    return mfk, ufk


class TestRunPurgeOnce(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.stub = _make_server_stub(self.path)

        raw_days = C.RETENTION_DAYS["raw_events"]  # 45
        now = dt.datetime.now(dt.timezone.utc)
        self.old_ts = (now - dt.timedelta(days=raw_days + 5)).isoformat()
        self.fresh_ts = now.isoformat()

        _seed_events(self.stub.conn, self.old_ts, self.fresh_ts)

    def tearDown(self):
        self.stub.conn.close()
        os.unlink(self.path)

    def test_old_event_deleted_fresh_kept(self):
        """run_purge_once removes events beyond raw_events retention and keeps fresh ones."""
        counts = api.run_purge_once(self.stub)

        # Returns a non-empty dict on success
        self.assertIsInstance(counts, dict)
        self.assertIn("activity_event", counts)

        remaining = self.stub.conn.execute(
            "SELECT client_event_id FROM activity_event"
        ).fetchall()
        ids = {r["client_event_id"] for r in remaining}

        self.assertNotIn("cid-old-001", ids, "old event should have been purged")
        self.assertIn("cid-fresh-001", ids, "fresh event must survive")

    def test_failed_purge_never_crashes(self):
        """§2 safety property: a purge that errors must not propagate — it returns {}.
        Closing the connection forces an OperationalError inside purge_expired."""
        self.stub.conn.close()  # any subsequent DB call now raises
        result = api.run_purge_once(self.stub)
        self.assertEqual(result, {})

    def test_constants_defined(self):
        """Module-level constants must be present and sensible."""
        self.assertEqual(api.PURGE_INTERVAL_S, 86400)
        self.assertGreater(api.PURGE_INITIAL_DELAY_S, 0)


if __name__ == "__main__":
    unittest.main()
