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
            "username": "root", "password": "pw", "org_name": "Acme", "mode": "coaching"})
        self.assertEqual(status, 200)
        self.assertEqual(body["role"], "admin")

        b2 = self._get("/api/v1/bootstrap-status")
        self.assertFalse(b2["needs_admin"])
        self.assertTrue(b2["first_run_complete"])
        self.assertEqual(b2["org_name"], "Acme")

        # second setup-admin must be refused
        status2, _ = self._post("/api/v1/setup-admin", {"username": "x", "password": "y"})
        self.assertEqual(status2, 403)


if __name__ == "__main__":
    unittest.main()
