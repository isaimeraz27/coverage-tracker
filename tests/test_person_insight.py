"""Person-page data: nested category breakdown + hourly timeline buckets."""
import os
import sys
import json
import threading
import tempfile
import unittest
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from server import api, db, rollup  # noqa: E402
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
        self.assertEqual([c["category"] for c in extra["breakdown"]][0], "work_comms")
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


class TestHourlyTimeline(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.conn = db.connect(self.path)
        db.init_db(self.conn)
        self.uid = _seed_user(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def test_buckets_by_clock_hour_and_includes_outside_window(self):
        day = "2026-06-09"
        _ev(self.conn, self.uid, day + "T09:15:00+00:00", "code", None, "dev_tools", 1800_000)
        _ev(self.conn, self.uid, day + "T09:40:00+00:00", "zoom", None, "meeting", 600_000, meeting=1)
        _ev(self.conn, self.uid, day + "T00:18:00+00:00", "msedge", "x.com", "social", 300_000)
        self.conn.commit()
        rows = self.conn.execute(
            "SELECT ts, sub_category, state, active_ms, idle_ms, is_meeting, app, domain "
            "FROM activity_event WHERE user_fk=? AND substr(ts_norm,1,10)=? ORDER BY ts_norm",
            (self.uid, day)).fetchall()
        out = rollup.hourly_buckets(rows, {"work_start": 8, "work_end": 18})
        self.assertEqual(out["work_start"], 8)
        self.assertEqual(out["work_end"], 18)
        by_hour = {h["hour"]: h for h in out["hours"]}
        self.assertEqual(by_hour[9]["productive_s"], 1800)
        self.assertEqual(by_hour[9]["meeting_s"], 600)
        self.assertIn(0, by_hour)
        self.assertEqual(by_hour[0]["distracting_s"], 300)

    def test_idle_and_meeting_while_idle_go_to_idle_bucket(self):
        day = "2026-06-09"
        # a plain idle row
        _ev(self.conn, self.uid, day + "T11:00:00+00:00", "", None, "idle", 0, idle_ms=300_000, state="idle")
        # a row marked meeting BUT in idle state must fall through to idle, not meeting
        _ev(self.conn, self.uid, day + "T11:30:00+00:00", "zoom", None, "meeting", 0, idle_ms=120_000, state="idle", meeting=1)
        self.conn.commit()
        rows = self.conn.execute(
            "SELECT ts, sub_category, state, active_ms, idle_ms, is_meeting, app, domain "
            "FROM activity_event WHERE user_fk=? AND substr(ts_norm,1,10)=? ORDER BY ts_norm",
            (self.uid, day)).fetchall()
        out = rollup.hourly_buckets(rows, {"work_start": 8, "work_end": 18})
        by_hour = {h["hour"]: h for h in out["hours"]}
        self.assertEqual(by_hour[11]["idle_s"], 420)     # 300 + 120
        self.assertEqual(by_hour[11]["meeting_s"], 0)    # meeting-while-idle did NOT count as meeting

    def test_hours_span_covers_work_window_even_when_empty(self):
        out = rollup.hourly_buckets([], {"work_start": 8, "work_end": 12})
        hours = [h["hour"] for h in out["hours"]]
        self.assertEqual(hours, [8, 9, 10, 11])


class TestPersonPayload(unittest.TestCase):
    """The person HTTP payload exposes `breakdown`, drops `tasks`, and serves the
    hourly-timeline dict — verified against a live server with a logged-in admin."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        pre = db.connect(self.path)
        db.init_db(pre)
        pre.close()
        self.srv = api.make_server(0, self.path)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        os.unlink(self.path)

    def _setup_admin(self):
        """First-run bootstrap: create the admin and return the session cookie."""
        req = urllib.request.Request(
            self.url + "/api/v1/setup-admin",
            data=json.dumps({"username": "boss", "password": "pw"}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            cookie = r.headers.get("Set-Cookie", "")
        return cookie.split(";")[0]  # "sid=..."

    def test_person_payload_exposes_breakdown_and_drops_tasks(self):
        cookie = self._setup_admin()
        day = "2026-06-09"
        with self.srv.lock:
            uid = _seed_user(self.srv.conn)
            _ev(self.srv.conn, uid, day + "T09:00:00+00:00", "code", None, "dev_tools", 900_000)
            self.srv.conn.commit()
        req = urllib.request.Request(
            self.url + f"/api/v1/person?uid={uid}&day={day}",
            headers={"Cookie": cookie})
        with urllib.request.urlopen(req) as r:
            body = json.loads(r.read())
        self.assertIn("breakdown", body)
        self.assertNotIn("tasks", body)
        self.assertIsInstance(body["timeline"], dict)
        self.assertIn("hours", body["timeline"])


if __name__ == "__main__":
    unittest.main()
