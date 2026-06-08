"""Workflow/task detection: complete vs incomplete quotes, reopens, sequence vs set
mode, long-idle splits. Pure-function tests over a hand-built resolved stream."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from insights import workflows  # noqa: E402


def row(sub, mins, app="chrome", idle=False, meeting=False):
    s = mins * 60
    return {"ts": f"2026-06-08T10:{mins:02d}:00", "sub": ("idle" if idle else sub),
            "coarse": "neutral", "is_meeting": meeting, "is_idle": idle,
            "active_s": 0 if idle else s, "idle_s": s if idle else 0,
            "app": app, "domain": sub}


def tmpl(mode="set_within_window", window_s=3600, expected=None,
         steps=("rating", "carrier_portal", "ams")):
    return [{"name": "quote", "match_mode": mode, "window_s": window_s,
             "expected_duration_s": expected, "steps": list(steps),
             "required": {s: True for s in steps}, "order": list(steps)}]


class TestDetect(unittest.TestCase):
    def test_complete_quote_matches(self):
        stream = [row("dialer", 5), row("rating", 10), row("carrier_portal", 5), row("ams", 5)]
        tasks = workflows.detect_tasks(stream, tmpl())
        self.assertEqual(len(tasks), 1)
        self.assertTrue(tasks[0]["matched"])
        self.assertEqual(sorted(tasks[0]["steps_hit"]), ["ams", "carrier_portal", "rating"])
        self.assertEqual(tasks[0]["steps_missing"], [])

    def test_incomplete_quote_not_matched(self):
        stream = [row("rating", 10), row("carrier_portal", 5)]  # no ams
        tasks = workflows.detect_tasks(stream, tmpl())
        self.assertEqual(len(tasks), 1)
        self.assertFalse(tasks[0]["matched"])
        self.assertEqual(tasks[0]["steps_missing"], ["ams"])

    def test_reopen_count(self):
        stream = [row("rating", 10), row("carrier_portal", 5), row("ams", 5),
                  row("crm", 5),
                  row("rating", 8), row("carrier_portal", 5), row("ams", 5)]
        tasks = workflows.detect_tasks(stream, tmpl())
        self.assertEqual(len(tasks), 2)
        self.assertTrue(all(t["reopen_count"] == 1 for t in tasks))

    def test_long_idle_splits(self):
        stream = [row("rating", 10), row("carrier_portal", 5),
                  row("away", 15, idle=True),   # 15-min idle > 10-min break threshold
                  row("ams", 5)]
        tasks = workflows.detect_tasks(stream, tmpl())
        # the idle breaks the task → first instance has no ams
        self.assertGreaterEqual(len(tasks), 1)
        self.assertFalse(tasks[0]["matched"])
        self.assertIn("ams", tasks[0]["steps_missing"])

    def test_sequence_mode_requires_order(self):
        # ams before rating → sequence fails, set mode passes
        stream = [row("ams", 5), row("rating", 10), row("carrier_portal", 5)]
        seq = workflows.detect_tasks(stream, tmpl(mode="sequence"))
        self.assertFalse(seq[0]["matched"])  # ams appeared before rating
        st = workflows.detect_tasks(stream, tmpl(mode="set_within_window"))
        self.assertTrue(st[0]["matched"])    # all present, order ignored

    def test_vs_expected_and_on_task(self):
        stream = [row("rating", 10), row("carrier_portal", 5), row("ams", 5)]  # 20 min
        tasks = workflows.detect_tasks(stream, tmpl(expected=600))  # expected 10 min
        self.assertAlmostEqual(tasks[0]["vs_expected"], 2.0, places=1)  # took 2x
        self.assertEqual(tasks[0]["on_task_ratio"], 1.0)  # all in-template

    def test_no_templates_no_tasks(self):
        stream = [row("rating", 10)]
        self.assertEqual(workflows.detect_tasks(stream, []), [])


if __name__ == "__main__":
    unittest.main()
