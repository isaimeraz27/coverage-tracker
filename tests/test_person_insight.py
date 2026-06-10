"""Person-page data: nested category breakdown + hourly timeline buckets."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from server import db, rollup  # noqa: E402
from shared import contracts as C  # noqa: E402


def _seed_user(conn):
    conn.execute("INSERT INTO machine(id, machine_id, hostname) VALUES(1,'m1','m1')")
    conn.execute("INSERT INTO role(name, target_score) VALUES('producer', NULL)")
    rid = conn.execute("SELECT id FROM role WHERE name='producer'").fetchone()["id"]
    conn.execute("INSERT INTO app_user(machine_fk, username, display_name, role_fk) "
                 "VALUES(1,'u','U',?)", (rid,))
    return conn.execute("SELECT id FROM app_user WHERE username='u'").fetchone()["id"]


def _ev(conn, uid, ts, app, domain, sub, active_ms, idle_ms=0, state="active", meeting=0):
    conn.execute(
        "INSERT INTO activity_event(user_fk,machine_fk,client_event_id,ts,ts_norm,app,domain,"
        "url,sub_category,category_code,state,active_ms,idle_ms,is_meeting) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (uid, 1, ts + app, ts, ts, app, domain, None, sub,
         C.coarse_of(sub).value, state, active_ms, idle_ms, meeting))


class TestBreakdown(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.conn = db.connect(self.path)
        db.init_db(self.conn)
        self.uid = _seed_user(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def test_breakdown_nests_domains_under_category_sorted(self):
        day = "2026-06-09"
        _ev(self.conn, self.uid, day + "T09:00:00+00:00", "msedge", "mail.google.com", "work_comms", 1200_000)
        _ev(self.conn, self.uid, day + "T09:30:00+00:00", "msedge", "outlook.office.com", "work_comms", 600_000)
        _ev(self.conn, self.uid, day + "T10:00:00+00:00", "code", None, "dev_tools", 900_000)
        self.conn.commit()
        _, extra, _ = rollup.build_ledger(self.conn, self.uid, day)
        bd = {c["category"]: c for c in extra["breakdown"]}
        self.assertIn("work_comms", bd)
        self.assertIn("dev_tools", bd)
        self.assertEqual(bd["work_comms"]["secs"], 1800)
        kids = bd["work_comms"]["children"]
        self.assertEqual(kids[0]["label"], "mail.google.com")
        self.assertEqual(kids[0]["secs"], 1200)
        self.assertEqual(kids[0]["kind"], "domain")
        self.assertEqual(kids[1]["label"], "outlook.office.com")
        self.assertEqual(bd["dev_tools"]["children"][0]["label"], "code")
        self.assertEqual(bd["dev_tools"]["children"][0]["kind"], "app")

    def test_breakdown_excludes_idle_and_keeps_top_flat_shape(self):
        day = "2026-06-09"
        _ev(self.conn, self.uid, day + "T09:00:00+00:00", "code", None, "dev_tools", 600_000)
        _ev(self.conn, self.uid, day + "T09:20:00+00:00", "", None, "idle", 0, idle_ms=600_000, state="idle")
        self.conn.commit()
        _, extra, _ = rollup.build_ledger(self.conn, self.uid, day)
        cats = [c["category"] for c in extra["breakdown"]]
        self.assertNotIn("idle", cats)
        self.assertTrue(all(set(t.keys()) == {"sub", "secs"} for t in extra["top"]))


if __name__ == "__main__":
    unittest.main()
