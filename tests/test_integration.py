"""End-to-end wire test: agent shipper -> ingest API -> DB -> rollup -> insight.

Starts the real server on an ephemeral port, enrolls via HTTP, ships synthetic
events, checks idempotency, then rolls up and asserts the insights. No real
surveillance — the data is synthetic (§5 testing strategy)."""
import os
import sys
import time
import threading
import datetime as dt
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from server import api, db, auth, rollup  # noqa: E402
from agent import shipper  # noqa: E402
from tools import synth  # noqa: E402

BUCKETS = {p["username"]: p["buckets"] for p in synth.PERSONAS}


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        pre = db.connect(self.path)
        db.init_db(pre)
        self.code = auth.issue_enrollment_code(pre, "ws-test-01")
        db.ensure_role(pre, "developer", synth.ROLES["developer"])
        pre.close()
        self.srv = api.make_server(0, self.path)
        self.port = self.srv.server_address[1]
        self.th = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.th.start()
        self.url = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        os.unlink(self.path)

    def _set_role_and_rollup(self, username, day):
        with self.srv.lock:
            conn = self.srv.conn
            uid = conn.execute("SELECT id FROM app_user WHERE username=?", (username,)).fetchone()["id"]
            rid = conn.execute("SELECT id FROM role WHERE name='developer'").fetchone()["id"]
            conn.execute("UPDATE app_user SET role_fk=? WHERE id=?", (rid, uid))
            conn.commit()
            return rollup.rollup_day(conn, uid, day)

    def test_wire_and_insight(self):
        token = shipper.enroll(self.url, self.code, "WS-TEST-01")
        self.assertTrue(token and token.startswith("eat_"))
        day = dt.date.today().isoformat()

        # ship Ana (good day) — and verify idempotency on a repeated batch
        evs = synth.build_events(day, BUCKETS["ana"])
        r1 = shipper.Shipper(self.url, token, "ana").post(evs)
        r2 = shipper.Shipper(self.url, token, "ana").post(evs)
        self.assertEqual(r1["accepted"], len(evs))
        self.assertEqual(r2["deduped"], len(evs))   # §3.7 client_event_id dedup

        ana = self._set_role_and_rollup("ana", day)
        self.assertGreater(ana.score, 70)
        self.assertGreater(ana.adherence, 0.80)
        self.assertFalse(ana.attention)

        # ship Beto (clearly wasting time) -> flagged
        shipper.Shipper(self.url, token, "beto").post(synth.build_events(day, BUCKETS["beto"]))
        beto = self._set_role_and_rollup("beto", day)
        self.assertTrue(beto.attention)
        self.assertIn("distracting_excess", {f.code for f in beto.flags})

        # ship Eva (partial day) -> low confidence, not for evaluation
        shipper.Shipper(self.url, token, "eva").post(synth.build_events(day, BUCKETS["eva"]))
        eva = self._set_role_and_rollup("eva", day)
        self.assertTrue(eva.needs_context)

    def test_unknown_token_rejected(self):
        r = shipper.Shipper(self.url, "eat_bogus_x", "ana")
        with self.assertRaises(Exception):
            r.post(synth.build_events(dt.date.today().isoformat(), BUCKETS["ana"]))


if __name__ == "__main__":
    unittest.main()
