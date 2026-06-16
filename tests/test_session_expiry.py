"""Session expiry (idle + absolute) + Secure cookie flag (§3c production hardening).

Cases:
  a. fresh session authenticates (200 on protected endpoint)
  b. past idle → rejected (401/403) + entry pruned from SESSIONS
  c. within idle but past absolute → rejected
  d. activity within idle keeps session alive and resets last_seen
  e. Secure flag present on https, absent on http
"""
from __future__ import annotations

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


def _spin_server():
    """Return a running test server on an ephemeral port, with fresh DB."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = db.connect(path)
    db.init_db(conn)
    conn.close()
    srv = api.make_server(0, path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, path


def _setup_admin(url, *, extra_headers=None):
    """POST setup-admin and return (status_code, response_body_dict, set_cookie_header)."""
    payload = json.dumps({
        "username": "root", "password": "pw",
        "org_name": "TestOrg", "enroll_password": "team-secret-1",
    }).encode()
    headers = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url + "/api/v1/setup-admin",
                                 data=payload, headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            body = json.loads(r.read())
            set_cookie = r.headers.get("Set-Cookie", "")
            return r.status, body, set_cookie
    except urllib.error.HTTPError as e:
        body = json.loads(e.read() or b"{}")
        set_cookie = e.headers.get("Set-Cookie", "") if hasattr(e, "headers") else ""
        return e.code, body, set_cookie


def _extract_sid(set_cookie: str) -> str:
    """Pull sid=<value> out of a Set-Cookie header."""
    for part in set_cookie.split(";"):
        part = part.strip()
        if part.startswith("sid="):
            return part[4:]
    raise ValueError(f"No sid= found in Set-Cookie: {set_cookie!r}")


def _get_me(url, sid):
    """GET /api/v1/me with the given sid cookie. Returns (status, body_dict)."""
    req = urllib.request.Request(url + "/api/v1/me",
                                 headers={"Cookie": f"sid={sid}"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


class TestSessionExpiry(unittest.TestCase):

    def setUp(self):
        self.real_now = api._now
        self.srv, self.path = _spin_server()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        api._now = self.real_now          # always restore before server shutdown
        self.srv.shutdown()
        self.srv.server_close()
        os.unlink(self.path)

    # ------------------------------------------------------------------ #
    # (a) Fresh session authenticates                                      #
    # ------------------------------------------------------------------ #
    def test_a_fresh_session_authenticates(self):
        base = 1_000_000.0
        api._now = lambda: base

        status, body, set_cookie = _setup_admin(self.url)
        self.assertEqual(status, 200)
        self.assertEqual(body["role"], "admin")

        sid = _extract_sid(set_cookie)
        status2, body2 = _get_me(self.url, sid)
        self.assertEqual(status2, 200)
        self.assertEqual(body2["username"], "root")

    # ------------------------------------------------------------------ #
    # (b) Past idle → rejected + entry pruned                             #
    # ------------------------------------------------------------------ #
    def test_b_past_idle_rejected_and_pruned(self):
        base = 2_000_000.0
        api._now = lambda: base

        _, _, set_cookie = _setup_admin(self.url)
        sid = _extract_sid(set_cookie)

        # Advance past idle timeout
        api._now = lambda: base + api.IDLE_TIMEOUT_S + 1

        status, _ = _get_me(self.url, sid)
        self.assertIn(status, (401, 403), "expired idle session must be rejected")

        # Entry must have been pruned
        self.assertNotIn(sid, api.SESSIONS, "expired sid must be removed from SESSIONS")

    # ------------------------------------------------------------------ #
    # (c) Within idle but past absolute → rejected                        #
    # ------------------------------------------------------------------ #
    def test_c_within_idle_but_past_absolute_rejected(self):
        # Strategy: login at base; directly backdate the session's 'created' to
        # simulate a session that was created 7d+1s ago but has been active recently
        # (last_seen = base, delta from now = 0). Then at base+1:
        #   - idle delta = 1s < IDLE → idle check passes
        #   - absolute delta from backdated created = ABSOLUTE+2 > ABSOLUTE → rejected
        base = 3_000_000.0
        api._now = lambda: base

        _, _, set_cookie = _setup_admin(self.url)
        sid = _extract_sid(set_cookie)

        # Verify the session is valid right now
        status_fresh, _ = _get_me(self.url, sid)
        self.assertEqual(status_fresh, 200, "session must be valid immediately after login")

        # Backdate 'created' to exceed ABSOLUTE: session is now ABSOLUTE+2 seconds old.
        # Use the server lock to avoid a race with the handler thread.
        with self.srv.lock:
            entry = api.SESSIONS.get(sid)
            self.assertIsNotNone(entry, "session entry must exist")
            entry["created"] = base - api.ABSOLUTE_TIMEOUT_S - 2
            # last_seen stays at base (set at login), so idle delta at base+1 = 1s < IDLE

        # Advance time by 1s: idle delta = 1s (fresh) but absolute delta = ABSOLUTE+3 → rejected
        api._now = lambda: base + 1
        status_exp, _ = _get_me(self.url, sid)
        self.assertIn(status_exp, (401, 403),
                      "absolute timeout must fire even with recent last_seen")

    # ------------------------------------------------------------------ #
    # (d) Activity within idle keeps session alive + resets last_seen     #
    # ------------------------------------------------------------------ #
    def test_d_activity_within_idle_keeps_session_alive(self):
        base = 4_000_000.0
        api._now = lambda: base

        _, _, set_cookie = _setup_admin(self.url)
        sid = _extract_sid(set_cookie)

        # First sub-idle request — refreshes last_seen
        gap = api.IDLE_TIMEOUT_S - 100  # < IDLE each step
        api._now = lambda: base + gap
        status1, _ = _get_me(self.url, sid)
        self.assertEqual(status1, 200, "first sub-idle request must succeed")

        # Second sub-idle request from the refreshed last_seen
        # last_seen is now base+gap; next request is at base+gap+gap = base+2*gap.
        # Without refresh this would be base + 2*gap from created — if 2*gap > IDLE
        # (which it is: 2*(IDLE-100) = 2*IDLE-200 > IDLE) a non-refreshing impl
        # would reject it. But we refreshed, so idle delta = gap < IDLE → 200.
        # Also ensure 2*gap < ABSOLUTE so we don't trip the absolute check.
        self.assertLess(2 * gap, api.ABSOLUTE_TIMEOUT_S, "gaps must be within absolute")
        api._now = lambda: base + 2 * gap
        status2, _ = _get_me(self.url, sid)
        self.assertEqual(status2, 200, "second sub-idle request must succeed (last_seen refreshed)")


class TestSecureCookieFlag(unittest.TestCase):
    """Secure flag on Set-Cookie: present for https, absent for http."""

    def _make_fresh_server(self):
        srv, path = _spin_server()
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        return srv, path, url

    def tearDown(self):
        # Servers are torn down per-test via addCleanup
        pass

    def test_e_secure_flag_on_https(self):
        srv, path, url = self._make_fresh_server()
        self.addCleanup(srv.shutdown)
        self.addCleanup(srv.server_close)
        self.addCleanup(os.unlink, path)

        _, _, set_cookie = _setup_admin(url, extra_headers={"X-Forwarded-Proto": "https"})
        self.assertIn("Secure", set_cookie,
                      f"Secure must appear in Set-Cookie for https; got: {set_cookie!r}")

    def test_e_no_secure_flag_on_http(self):
        srv, path, url = self._make_fresh_server()
        self.addCleanup(srv.shutdown)
        self.addCleanup(srv.server_close)
        self.addCleanup(os.unlink, path)

        # No X-Forwarded-Proto header → defaults to http
        _, _, set_cookie = _setup_admin(url)
        self.assertNotIn("Secure", set_cookie,
                         f"Secure must NOT appear for plain http; got: {set_cookie!r}")
