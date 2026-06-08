import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from shared import contracts as C  # noqa: E402


class TestCategorize(unittest.TestCase):
    def test_process(self):
        self.assertEqual(C.categorize("code", None), ("dev_tools", False))

    def test_domain_wins(self):
        self.assertEqual(C.categorize("chrome", "github.com"), ("code_review", False))
        self.assertEqual(C.categorize("chrome", "youtube.com"), ("streaming", False))

    def test_meeting(self):
        self.assertEqual(C.categorize("zoom", None), ("meeting", True))
        self.assertEqual(C.categorize("chrome", "meet.google.com"), ("meeting", True))

    def test_unknown(self):
        self.assertEqual(C.categorize("weirdapp", None), ("uncategorized", False))


class TestHelpers(unittest.TestCase):
    def test_registrable_domain(self):
        self.assertEqual(C.registrable_domain("https://github.com/a/b?x=1"), "github.com")
        self.assertEqual(C.registrable_domain("mail.google.com"), "google.com")

    def test_token_roundtrip(self):
        t = C.make_agent_token("ws-07")
        self.assertTrue(t.startswith("eat_"))
        self.assertEqual(C.token_machine_id(t), "ws-07")

    def test_coarse(self):
        self.assertEqual(C.coarse_of("social"), C.CoarseClass.DISTRACTING)
        self.assertEqual(C.coarse_of("dev_tools"), C.CoarseClass.PRODUCTIVE)

    def test_normalize(self):
        self.assertEqual(C.normalize_machine_id("WS-07!"), "ws-07")
        self.assertEqual(C.normalize_username("  JDoe "), "jdoe")


if __name__ == "__main__":
    unittest.main()
