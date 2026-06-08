import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agent import redaction  # noqa: E402
from agent.capture import Sample  # noqa: E402


def s(proc, title=None, dom=None, url=None):
    return Sample(ts=0.0, process=proc, window_title=title, domain=dom, idle_ms=0, url=url)


class TestRedaction(unittest.TestCase):
    def test_sensitive_app_dropped(self):
        self.assertIsNone(redaction.redact_sample(s("keepass")))
        self.assertIsNone(redaction.redact_sample(s("1password")))

    def test_password_title_redacted(self):
        out = redaction.redact_sample(s("chrome", title="Sign in to your bank"))
        self.assertEqual(out.window_title, "[redacted]")

    def test_url_stripped_to_domain(self):
        out = redaction.redact_sample(s("chrome", dom="sub.github.com/secret/path"), allow_full_url=False)
        self.assertEqual(out.domain, "github.com")

    def test_full_url_allowed_when_enabled(self):
        # full URL goes in the dedicated `url` field; domain stays registrable
        out = redaction.redact_sample(
            s("chrome", dom="app.github.com", url="https://app.github.com/quotes/5/rating"),
            allow_full_url=True)
        self.assertEqual(out.domain, "github.com")
        self.assertEqual(out.url, "https://app.github.com/quotes/5/rating")

    def test_full_url_dropped_when_disabled(self):
        out = redaction.redact_sample(
            s("chrome", dom="app.github.com", url="https://app.github.com/quotes/5/rating"),
            allow_full_url=False)
        self.assertEqual(out.domain, "github.com")
        self.assertIsNone(out.url)

    def test_sensitive_domain_url_dropped_even_when_full_url_enabled(self):
        # banking/health/identity: keep the domain, never the path
        out = redaction.redact_sample(
            s("chrome", dom="secure.chase.com", url="https://secure.chase.com/accounts/12345"),
            allow_full_url=True)
        self.assertEqual(out.domain, "chase.com")
        self.assertIsNone(out.url)

    def test_engagement_counters_survive_redaction(self):
        sample = Sample(ts=0.0, process="code", window_title="x", domain=None, idle_ms=0,
                        key_count=42, mouse_count=7, mouse_distance_px=900)
        out = redaction.redact_sample(sample)
        self.assertIsNotNone(out)
        self.assertEqual((out.key_count, out.mouse_count, out.mouse_distance_px), (42, 7, 900))

    def test_sensitive_app_drops_even_with_counts(self):
        sample = Sample(ts=0.0, process="keepass", window_title="x", domain=None, idle_ms=0,
                        key_count=42, mouse_count=7, mouse_distance_px=900)
        self.assertIsNone(redaction.redact_sample(sample))


if __name__ == "__main__":
    unittest.main()
