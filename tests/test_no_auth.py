"""TRACKER_NO_AUTH strict gate — unit + integration tests.

Spec: NO_AUTH must be True *only* when the env var is exactly "1".
Any other non-empty value ("0", "false", "yes", …) must leave it False.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from server import api, db  # noqa: E402


def _reload_no_auth(value: str | None) -> bool:
    """Set (or clear) TRACKER_NO_AUTH, reload server.api, return the parsed NO_AUTH value.
    Always restores the env-var to its previous state so tests can't leak to each other.
    """
    prev = os.environ.get("TRACKER_NO_AUTH")
    try:
        if value is None:
            os.environ.pop("TRACKER_NO_AUTH", None)
        else:
            os.environ["TRACKER_NO_AUTH"] = value
        reloaded = importlib.reload(api)
        return reloaded.NO_AUTH
    finally:
        # restore
        if prev is None:
            os.environ.pop("TRACKER_NO_AUTH", None)
        else:
            os.environ["TRACKER_NO_AUTH"] = prev
        # reload once more so the module goes back to whatever the env has now
        importlib.reload(api)


class TestNoAuthParsing(unittest.TestCase):
    """Unit tests: the parse expression `env == "1"` must be strict."""

    def test_empty_string_is_off(self):
        self.assertFalse(_reload_no_auth(""),
                         "TRACKER_NO_AUTH='' must leave NO_AUTH False")

    def test_zero_string_is_off(self):
        self.assertFalse(_reload_no_auth("0"),
                         "TRACKER_NO_AUTH='0' must leave NO_AUTH False (the old bool() bug)")

    def test_false_string_is_off(self):
        self.assertFalse(_reload_no_auth("false"),
                         "TRACKER_NO_AUTH='false' must leave NO_AUTH False")

    def test_unset_is_off(self):
        self.assertFalse(_reload_no_auth(None),
                         "Unset TRACKER_NO_AUTH must leave NO_AUTH False")

    def test_one_string_is_on(self):
        self.assertTrue(_reload_no_auth("1"),
                        "TRACKER_NO_AUTH='1' must set NO_AUTH True")


class TestNoAuthHTTP(unittest.TestCase):
    """Integration: when NO_AUTH is OFF, protected endpoints reject unauthenticated requests."""

    def setUp(self):
        # Ensure NO_AUTH is OFF for the live-server tests.
        os.environ.pop("TRACKER_NO_AUTH", None)
        importlib.reload(api)  # ensure module reflects env

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
        # Belt-and-suspenders: reload with no env var so module is clean for next suite.
        os.environ.pop("TRACKER_NO_AUTH", None)
        importlib.reload(api)

    def _get_status(self, path: str) -> int:
        try:
            with urllib.request.urlopen(self.url + path) as r:
                return r.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def test_admin_endpoint_requires_auth(self):
        """GET /api/v1/admin/users must be 403 (admin only) when NO_AUTH is OFF."""
        status = self._get_status("/api/v1/admin/users")
        self.assertEqual(status, 403,
                         f"Expected 403 from /api/v1/admin/users without auth, got {status}")

    def test_me_returns_401_without_session(self):
        """GET /api/v1/me must be 401 when NO_AUTH is OFF and no cookie is present."""
        status = self._get_status("/api/v1/me")
        self.assertEqual(status, 401,
                         f"Expected 401 from /api/v1/me without auth, got {status}")

    def test_no_auth_on_bypasses_admin_gate(self):
        """With NO_AUTH forced ON (api.NO_AUTH = True), the admin endpoint accepts the request."""
        orig = api.NO_AUTH
        api.NO_AUTH = True
        self.addCleanup(setattr, api, "NO_AUTH", orig)
        # Now the server handler reads api.NO_AUTH at request time via the module reference.
        # NOTE: _session() references the module-level NO_AUTH name directly, so patching
        # the attribute is sufficient here (we're testing the HTTP path, not the parse).
        status = self._get_status("/api/v1/admin/users")
        self.assertEqual(status, 200,
                         f"Expected 200 from /api/v1/admin/users with NO_AUTH=True, got {status}")


if __name__ == "__main__":
    unittest.main()
