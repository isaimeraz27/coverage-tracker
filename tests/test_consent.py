"""§4 backend: ack_record migration, /disclosure endpoint, enroll records ack.

Cases:
  1. Migration v5 / ack_record table exists after init_db, LATEST >= 5.
  2. GET /api/v1/disclosure is public (no auth) and returns {version: int, text: str}.
  3. Acknowledged enroll writes a bound ack recording the SERVER's version (not the client's).
  4. Enroll WITHOUT disclosure_version still works + records disclosure_version=null.
  5. Auto-bump: PUT disclosure_text bumps version; same text does not bump; new text bumps again.
  6. disclosure_version cannot be set directly via PUT /api/v1/settings.
"""
from __future__ import annotations

import os
import sys
import json
import http.cookiejar
import tempfile
import threading
import unittest
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from server import api, db, auth, migrations  # noqa: E402


def _make_server(path):
    pre = db.connect(path)
    db.init_db(pre)
    pre.close()
    srv = api.make_server(0, path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class TestConsentMigration(unittest.TestCase):
    """Case 1: migration v5 + ack_record table."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

    def tearDown(self):
        os.unlink(self.path)

    def test_ack_record_table_exists_after_init(self):
        conn = db.connect(self.path)
        db.init_db(conn)
        # PRAGMA table_info returns rows for known tables, empty for unknown
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(ack_record)").fetchall()}
        self.assertIn("id", cols)
        self.assertIn("machine_id", cols)
        self.assertIn("hostname", cols)
        self.assertIn("disclosure_version", cols)
        self.assertIn("acknowledged_ts", cols)
        conn.close()

    def test_latest_migration_is_at_least_v5(self):
        self.assertGreaterEqual(migrations.LATEST, 5)

    def test_current_version_reaches_latest_after_init(self):
        conn = db.connect(self.path)
        db.init_db(conn)
        self.assertEqual(migrations.current_version(conn), migrations.LATEST)
        conn.close()

    def test_ack_record_insert_and_select(self):
        conn = db.connect(self.path)
        db.init_db(conn)
        conn.execute(
            "INSERT INTO ack_record(machine_id, hostname, disclosure_version, acknowledged_ts) "
            "VALUES (?,?,?,?)",
            ("test-machine", "host-x", 1, db.now_iso()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM ack_record WHERE machine_id=?", ("test-machine",)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["disclosure_version"], 1)
        conn.close()


class TestConsentHttp(unittest.TestCase):
    """Cases 2–6: HTTP-facing tests using a live server."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.srv = _make_server(self.path)
        self.port = self.srv.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        # Admin cookie jar (used for authenticated PUT /settings calls)
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        # Bootstrap an admin so we can call PUT /api/v1/settings
        self._setup_admin()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        os.unlink(self.path)

    def _setup_admin(self):
        req = urllib.request.Request(
            self.url + "/api/v1/setup-admin",
            data=json.dumps({
                "username": "root", "password": "pw",
                "enroll_password": "team-secret-1",
            }).encode(),
            headers={"Content-Type": "application/json"},
        )
        with self.opener.open(req) as r:
            body = json.loads(r.read())
        assert body["role"] == "admin", f"setup-admin failed: {body}"

    def _get(self, path):
        with urllib.request.urlopen(self.url + path) as r:
            return r.status, json.loads(r.read())

    def _put(self, path, obj):
        req = urllib.request.Request(
            self.url + path,
            data=json.dumps(obj).encode(),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        try:
            with self.opener.open(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def _post_json(self, path, obj):
        req = urllib.request.Request(
            self.url + path,
            data=json.dumps(obj).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    # -- Case 2: GET /api/v1/disclosure is public ------------------------------- #

    def test_disclosure_endpoint_public_no_auth(self):
        """Case 2: /disclosure returns {version: int, text: str} without any auth cookie."""
        # Plain urllib.request (no cookie jar) — must not need authentication
        st, body = self._get("/api/v1/disclosure")
        self.assertEqual(st, 200)
        self.assertIsInstance(body["version"], int)
        self.assertGreaterEqual(body["version"], 1)
        self.assertIsInstance(body["text"], str)
        self.assertGreater(len(body["text"]), 0)

    # -- Case 3: Enroll WITH disclosure_version writes bound ack ---------------- #

    def test_enroll_acknowledged_records_server_version_not_client_value(self):
        """Case 3: an acknowledged enroll records the SERVER's current disclosure_version,
        never the client-supplied number (which can't be trusted). Bump the server to v2,
        then enroll sending a bogus 999 — the ack must store 2, not 999."""
        # Bump the served version to 2 by editing the disclosure text (admin).
        st, _ = self._put("/api/v1/settings", {"disclosure_text": "Updated monitoring notice v2."})
        self.assertEqual(st, 200)
        _, disc = self._get("/api/v1/disclosure")
        self.assertEqual(disc["version"], 2)

        with self.srv.lock:
            code = auth.issue_enrollment_code(self.srv.conn, machine_id=None, label="host-x")

        st, body = self._post_json("/api/v1/enroll", {
            "code": code,
            "hostname": "host-x",
            "disclosure_version": 999,   # a forged/stale client value — must be ignored
        })
        self.assertEqual(st, 200)
        self.assertTrue(body["token"].startswith("eat_"))

        with self.srv.lock:
            row = self.srv.conn.execute(
                "SELECT machine_id, hostname, disclosure_version, acknowledged_ts "
                "FROM ack_record WHERE hostname=?", ("host-x",)
            ).fetchone()

        self.assertIsNotNone(row, "ack_record row must exist after enroll")
        self.assertEqual(row["disclosure_version"], 2,
                          "ack must record the server's served version, not the client's 999")
        self.assertIsNotNone(row["acknowledged_ts"])

    # -- Case 4: Enroll WITHOUT disclosure_version records null version --------- #

    def test_enroll_without_disclosure_version_backward_compat(self):
        """Case 4: POST /enroll without disclosure_version -> 200 + null ack version."""
        with self.srv.lock:
            code = auth.issue_enrollment_code(self.srv.conn, machine_id=None, label="host-y")

        st, body = self._post_json("/api/v1/enroll", {
            "code": code,
            "hostname": "host-y",
        })
        self.assertEqual(st, 200)
        self.assertTrue(body["token"].startswith("eat_"))

        with self.srv.lock:
            row = self.srv.conn.execute(
                "SELECT disclosure_version, acknowledged_ts "
                "FROM ack_record WHERE hostname=?", ("host-y",)
            ).fetchone()

        self.assertIsNotNone(row, "ack_record row must exist even for legacy enroll")
        self.assertIsNone(row["disclosure_version"],
                          "disclosure_version must be NULL for legacy enroll")
        self.assertIsNotNone(row["acknowledged_ts"])

    # -- Case 5: Auto-bump disclosure_version ----------------------------------- #

    def test_auto_bump_on_disclosure_text_change(self):
        """Case 5: PUT disclosure_text bumps version; same text no-op; new text bumps again."""
        # Starting version must be 1
        _, disc = self._get("/api/v1/disclosure")
        self.assertEqual(disc["version"], 1)

        # Change text -> version becomes 2
        st, _ = self._put("/api/v1/settings", {
            "disclosure_text": "New monitoring notice for the team."
        })
        self.assertEqual(st, 200)
        _, disc2 = self._get("/api/v1/disclosure")
        self.assertEqual(disc2["version"], 2)

        # Same text again -> version stays 2
        st, _ = self._put("/api/v1/settings", {
            "disclosure_text": "New monitoring notice for the team."
        })
        self.assertEqual(st, 200)
        _, disc3 = self._get("/api/v1/disclosure")
        self.assertEqual(disc3["version"], 2)

        # Different text -> version becomes 3
        st, _ = self._put("/api/v1/settings", {
            "disclosure_text": "Updated notice: still no keylogging, ever."
        })
        self.assertEqual(st, 200)
        _, disc4 = self._get("/api/v1/disclosure")
        self.assertEqual(disc4["version"], 3)

    # -- Case 6: disclosure_version cannot be set directly via PUT settings ----- #

    def test_disclosure_version_not_settable_directly(self):
        """Case 6: PUT {disclosure_version: 999} must not change the stored version."""
        _, disc_before = self._get("/api/v1/disclosure")
        ver_before = disc_before["version"]

        # PUT with disclosure_version directly — should be silently ignored (not in allowed)
        st, _ = self._put("/api/v1/settings", {"disclosure_version": 999})
        self.assertEqual(st, 200)

        _, disc_after = self._get("/api/v1/disclosure")
        self.assertEqual(disc_after["version"], ver_before,
                         "disclosure_version must not be settable by the client")

    # -- Seam: the full enroll -> hashed-token verify -> ack -> visible-on-/machines chain -- #

    def test_enroll_hash_ack_machines_roundtrip(self):
        """Cross-cutting seam (§3a token-hash × §4 consent): one acknowledged enroll must
        (a) mint a raw token that verify_agent_token resolves against the STORED HASH,
        (b) record a consent ack in the same commit, and
        (c) surface that consent on the admin /machines view — all from one enroll."""
        with self.srv.lock:
            code = auth.issue_enrollment_code(self.srv.conn, machine_id=None, label="host-rt")

        st, body = self._post_json("/api/v1/enroll", {
            "code": code, "hostname": "host-rt", "disclosure_version": 1,
        })
        self.assertEqual(st, 200)
        raw_token = body["token"]
        self.assertTrue(raw_token.startswith("eat_"))

        with self.srv.lock:
            # (a) §3a: the raw token verifies, and what's stored is the hash, not the raw.
            mfk = auth.verify_agent_token(self.srv.conn, raw_token)
            self.assertIsNotNone(mfk, "freshly enrolled raw token must verify (hash path)")
            stored = self.srv.conn.execute(
                "SELECT token, machine_id FROM machine WHERE id=?", (mfk,)).fetchone()
            self.assertEqual(stored["token"], auth.hash_token(raw_token))
            self.assertNotEqual(stored["token"], raw_token)
            # (b) §4: an ack row was written for this machine in the same enroll.
            ack = self.srv.conn.execute(
                "SELECT disclosure_version FROM ack_record WHERE machine_id=?",
                (stored["machine_id"],)).fetchone()
            self.assertIsNotNone(ack, "enroll must record an ack in the same commit")
            self.assertEqual(ack["disclosure_version"], 1)

        # (c) the admin /machines view surfaces the consent (authenticated GET via the cookie jar).
        with self.opener.open(self.url + "/api/v1/admin/machines") as r:
            machines = json.loads(r.read())["machines"]
        row = next((m for m in machines if m["hostname"] == "host-rt"), None)
        self.assertIsNotNone(row, "enrolled machine must appear on /machines")
        self.assertEqual(row["consent_version"], 1)
        self.assertIsNotNone(row["consented_ts"])


if __name__ == "__main__":
    unittest.main()
