"""Admin onboarding API: create+calibrate a role, issue an enrollment code, and confirm
that code actually enrolls an agent. Uses a cookie jar to carry the admin session."""
import os
import sys
import json
import threading
import tempfile
import unittest
import urllib.request
import urllib.error
import http.cookiejar

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from server import api, db  # noqa: E402


class TestAdminApi(unittest.TestCase):
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
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        os.unlink(self.path)

    def _req(self, method, path, obj=None):
        data = json.dumps(obj).encode() if obj is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        req = urllib.request.Request(self.url + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_role_calibration_and_enrollment(self):
        # become admin (cookie stored in the jar)
        st, _ = self._req("POST", "/api/v1/setup-admin",
                          {"username": "root", "password": "pw", "org_name": "Acme",
                           "enroll_password": "team-secret-123"})
        self.assertEqual(st, 200)

        # create a calibrated role
        st, body = self._req("POST", "/api/v1/admin/roles",
                             {"name": "developer", "on_task_set": ["dev_tools"], "target_score": 75})
        self.assertEqual(st, 200)

        st, roles = self._req("GET", "/api/v1/admin/roles")
        dev = next(r for r in roles["roles"] if r["name"] == "developer")
        self.assertEqual(dev["target_score"], 75.0)
        self.assertTrue(dev["calibrated"])
        self.assertIsNotNone(dev["calibrated_ts"])

        # uncalibrate it again (clearing the target)
        st, _ = self._req("PUT", f"/api/v1/admin/roles/{dev['id']}", {"target_score": None})
        self.assertEqual(st, 200)
        st, roles2 = self._req("GET", "/api/v1/admin/roles")
        dev2 = next(r for r in roles2["roles"] if r["id"] == dev["id"])
        self.assertFalse(dev2["calibrated"])
        self.assertIsNone(dev2["target_score"])

        # issue an enrollment code and use it to enroll an agent
        st, code = self._req("POST", "/api/v1/admin/enroll-code", {"label": "sam"})
        self.assertEqual(st, 200)
        self.assertIn("one_liner", code)
        st, tok = self._req("POST", "/api/v1/enroll", {"code": code["code"], "hostname": "ws-1"})
        self.assertEqual(st, 200)
        self.assertTrue(tok["token"].startswith("eat_"))

    def test_admin_requires_admin_session(self):
        # without a session, admin reads are 401 (not authenticated)
        fresh = urllib.request.build_opener()  # no cookie jar
        req = urllib.request.Request(self.url + "/api/v1/admin/roles")
        try:
            fresh.open(req)
            self.fail("expected an auth error")
        except urllib.error.HTTPError as e:
            self.assertIn(e.code, (401, 403))


if __name__ == "__main__":
    unittest.main()
