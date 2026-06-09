"""The persistent-incomplete-workflow coaching nudge: a producer who STARTS quote
workflows but doesn't complete them (e.g. never saves to the AMS) only gets flagged when
the pattern PERSISTS (>=3 of the last 5 days) — a single incomplete day is normal and must
not nag. Coaching-only / info severity; never a verdict."""
import os
import sys
import types
import uuid
import datetime as dt
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from server import db, api  # noqa: E402


class _H:
    def __init__(self, conn):
        self.conn = conn


class TestIncompleteNudge(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.conn = db.connect(self.path)
        db.init_db(self.conn)
        c = self.conn
        rid = db.ensure_role(c, "producer", ["rating", "carrier_portal", "ams"])
        for sub in ("rating", "carrier_portal", "ams"):
            c.execute("INSERT OR IGNORE INTO category(sub_category,coarse_class) VALUES(?,?)", (sub, "neutral"))
        db.ensure_workflow_template(
            c, "new_business_quote", "set_within_window", 2400,
            [("rating", True, 0), ("carrier_portal", True, 1), ("ams", True, 2)],
            expected_duration_s=2400)
        for mt, pat, sub in [("domain", "rater.com", "rating"),
                             ("domain", "carrier.com", "carrier_portal"),
                             ("domain", "ams.com", "ams")]:
            c.execute("INSERT INTO taxonomy_rule(match_type,pattern,sub_category,priority,created_ts,updated_ts) "
                      "VALUES(?,?,?,?,?,?)", (mt, pat, sub, 60, db.now_iso(), db.now_iso()))
        self.mfk = db.resolve_machine(c, "ws1", auto_provision=True, hostname="WS1")
        self.ufk = db.resolve_user(c, self.mfk, "sam", auto_provision=True)
        c.execute("UPDATE app_user SET role_fk=? WHERE id=?", (rid, self.ufk))
        c.commit()
        self.today = dt.date.today()
        self.nudge = types.MethodType(api.Handler._incomplete_workflow_nudge, _H(c))

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def _add(self, day, domain, mins):
        ts = day + "T10:00:00+00:00"
        self.conn.execute(
            "INSERT INTO activity_event(machine_fk,user_fk,client_event_id,ts,ts_norm,app,domain,url,"
            "sub_category,category_code,state,active_ms,idle_ms,is_meeting) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.mfk, self.ufk, str(uuid.uuid4()), ts, ts, "chrome", domain,
             "https://" + domain + "/x", "uncategorized", "neutral", "active", mins * 60000, 0, 0))
        self.conn.commit()

    def _incomplete_day(self, day):  # rating + carrier, no ams
        self._add(day, "rater.com", 25)
        self._add(day, "carrier.com", 15)

    def test_single_incomplete_day_does_not_fire(self):
        d0 = self.today.isoformat()
        self._incomplete_day(d0)
        self.assertIsNone(self.nudge(self.ufk, d0))

    def test_persistent_incomplete_fires(self):
        d0 = self.today.isoformat()
        for i in range(3):
            self._incomplete_day((self.today - dt.timedelta(days=i)).isoformat())
        n = self.nudge(self.ufk, d0)
        self.assertIsNotNone(n)
        self.assertGreaterEqual(n["days"], 3)
        self.assertIn("new_business_quote", n["templates"])

    def test_completing_workflows_clears_nudge(self):
        d0 = self.today.isoformat()
        for i in range(3):
            self._incomplete_day((self.today - dt.timedelta(days=i)).isoformat())
        self.assertIsNotNone(self.nudge(self.ufk, d0))
        for i in range(3):  # add the missing ams step each day → complete
            self._add((self.today - dt.timedelta(days=i)).isoformat(), "ams.com", 10)
        self.assertIsNone(self.nudge(self.ufk, d0))


if __name__ == "__main__":
    unittest.main()
