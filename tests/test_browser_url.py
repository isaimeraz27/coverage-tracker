"""The browser-URL reader must be FAIL-SAFE: when comtypes is absent (every non-Windows
host, and Windows hosts without it installed) it returns None instead of raising, so the
agent falls back to title-based domains. Also checks URL normalization."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agent import browser_url  # noqa: E402


class TestBrowserUrl(unittest.TestCase):
    def test_unavailable_without_comtypes(self):
        # On this host comtypes/UIA is absent → reader reports unavailable, never raises.
        r = browser_url.UrlReader()
        self.assertFalse(r.available)
        self.assertIsNone(r.read(12345))     # arbitrary hwnd → safe None
        self.assertIsNone(r.read(None))

    def test_one_shot_safe(self):
        self.assertIsNone(browser_url.read_active_url(None))
        self.assertIsNone(browser_url.read_active_url(999))

    def test_normalize_adds_scheme(self):
        n = browser_url.UrlReader._normalize
        self.assertEqual(n("app.ezlynx.com/quotes/5/rating"), "https://app.ezlynx.com/quotes/5/rating")
        self.assertEqual(n("https://ams.example.com/p/9"), "https://ams.example.com/p/9")
        self.assertIsNone(n(""))
        self.assertIsNone(n(None))


class TestAgentBundle(unittest.TestCase):
    def test_browser_url_in_bundle(self):
        from server import api
        self.assertIn("agent/browser_url.py", api.AGENT_BUNDLE)


if __name__ == "__main__":
    unittest.main()
