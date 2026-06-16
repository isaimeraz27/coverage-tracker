"""First-run bootstrap: an empty org reports needs_admin, setup-admin flips it and can
only run once."""
import os
import sys
import json
import threading
import tempfile
import unittest
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from server import api, db  # noqa: E402


class TestBootstrap(unittest.TestCase):
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

    def _get(self, path):
        with urllib.request.urlopen(self.url + path) as r:
            return json.loads(r.read())

    def _post(self, path, obj):
        req = urllib.request.Request(self.url + path, data=json.dumps(obj).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_bootstrap_flips_and_guards(self):
        b = self._get("/api/v1/bootstrap-status")
        self.assertTrue(b["needs_admin"])
        self.assertFalse(b["first_run_complete"])

        status, body = self._post("/api/v1/setup-admin", {
            "username": "root", "password": "pw", "org_name": "Acme", "mode": "coaching",
            "enroll_password": "team-secret-123"})
        self.assertEqual(status, 200)
        self.assertEqual(body["role"], "admin")

        b2 = self._get("/api/v1/bootstrap-status")
        self.assertFalse(b2["needs_admin"])
        self.assertTrue(b2["first_run_complete"])
        self.assertEqual(b2["org_name"], "Acme")

        # second setup-admin must be refused
        status2, _ = self._post("/api/v1/setup-admin", {"username": "x", "password": "y"})
        self.assertEqual(status2, 403)

    def test_setup_rejects_missing_enroll_password(self):
        """setup-admin without enroll_password → 400; no admin row created."""
        status, body = self._post("/api/v1/setup-admin", {
            "username": "root", "password": "pw", "org_name": "Acme"})
        self.assertEqual(status, 400)
        self.assertIn("non-default setup password", body.get("error", ""))
        # Prove the rejected attempt did NOT create an admin (needs_admin still true).
        b = self._get("/api/v1/bootstrap-status")
        self.assertTrue(b["needs_admin"], "rejected setup must not create an admin row")
        # Also confirm a subsequent valid setup still succeeds (no half-complete state).
        status2, body2 = self._post("/api/v1/setup-admin", {
            "username": "root", "password": "pw", "org_name": "Acme",
            "enroll_password": "real-password-99"})
        self.assertEqual(status2, 200)
        self.assertEqual(body2["role"], "admin")

    def test_setup_rejects_empty_enroll_password(self):
        """setup-admin with an empty enroll_password string → 400."""
        status, body = self._post("/api/v1/setup-admin", {
            "username": "root", "password": "pw", "enroll_password": ""})
        self.assertEqual(status, 400)
        self.assertIn("non-default setup password", body.get("error", ""))
        b = self._get("/api/v1/bootstrap-status")
        self.assertTrue(b["needs_admin"])

    def test_setup_rejects_whitespace_only_enroll_password(self):
        """A whitespace-only enroll_password strips to empty → 400 (locks in .strip())."""
        status, body = self._post("/api/v1/setup-admin", {
            "username": "root", "password": "pw", "enroll_password": "   "})
        self.assertEqual(status, 400)
        self.assertIn("non-default setup password", body.get("error", ""))
        b = self._get("/api/v1/bootstrap-status")
        self.assertTrue(b["needs_admin"])

    def test_setup_rejects_default_enroll_password(self):
        """setup-admin with the public seed 'coverage-setup' → 400; no admin created."""
        status, body = self._post("/api/v1/setup-admin", {
            "username": "root", "password": "pw", "enroll_password": "coverage-setup"})
        self.assertEqual(status, 400)
        self.assertIn("non-default setup password", body.get("error", ""))
        b = self._get("/api/v1/bootstrap-status")
        self.assertTrue(b["needs_admin"])

    def test_setup_stores_enroll_password(self):
        """setup-admin with a real password → 200 and the setting is persisted."""
        status, _ = self._post("/api/v1/setup-admin", {
            "username": "root", "password": "pw", "org_name": "Acme",
            "enroll_password": "my-team-pw-42"})
        self.assertEqual(status, 200)
        with self.srv.lock:
            stored = db.get_setting(self.srv.conn, "enroll_password")
        self.assertEqual(stored, "my-team-pw-42")


if __name__ == "__main__":
    unittest.main()
