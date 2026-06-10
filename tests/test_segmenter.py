import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agent.agent import Segmenter  # noqa: E402
from agent.capture import Sample  # noqa: E402


def mk(proc, idle_ms, dom=None):
    return Sample(ts=0.0, process=proc, window_title=None, domain=dom, idle_ms=idle_ms,
                  key_count=1, mouse_count=1, mouse_distance_px=10)


class TestSegmenter(unittest.TestCase):
    def test_focus_idle_meeting(self):
        seg = Segmenter(idle_gap_s=180)
        events = []
        events += seg.feed(mk("code", 0), 5)            # open dev_tools
        events += seg.feed(mk("code", 0), 5)
        e = seg.feed(mk("chrome", 0, "github.com"), 5)  # switch -> close dev_tools(10s)
        self.assertEqual(len(e), 1)
        self.assertEqual(e[0]["sub_category"], "dev_tools")
        self.assertEqual(e[0]["active_ms"], 10000)
        self.assertEqual(e[0]["state"], "active")
        self.assertEqual(e[0]["mouse_distance_px"], 20)   # 2 active samples x 10px

        e = seg.feed(mk("chrome", 200000, "github.com"), 5)  # idle -> close code_review(5s)
        self.assertEqual(e[0]["sub_category"], "code_review")
        seg.feed(mk("explorer", 200000), 5)                  # accumulate idle
        e = seg.feed(mk("zoom", 0), 5)                       # active -> close idle span
        self.assertEqual(e[0]["state"], "idle")
        self.assertEqual(e[0]["idle_ms"], 10000)
        self.assertEqual(e[0]["active_ms"], 0)

        fin = seg.flush()                                    # close meeting segment
        self.assertTrue(fin[0]["is_meeting"])
        self.assertEqual(fin[0]["sub_category"], "meeting")

    def test_flush_is_reusable_for_periodic_checkpoint(self):
        """flush() must be safe to call mid-stream, not just once at exit. After
        checkpointing an open segment, the next feed() opens a FRESH segment and emits
        no garbage (ts=None) event. This is what lets the runtime flush long single-app
        sessions so their data lands and survives an abrupt process kill."""
        seg = Segmenter(idle_gap_s=180)
        seg.feed(mk("code", 0), 5)
        seg.feed(mk("code", 0), 5)
        chunk = seg.flush()                       # checkpoint the open dev_tools segment
        self.assertEqual(len(chunk), 1)
        self.assertEqual(chunk[0]["sub_category"], "dev_tools")
        self.assertEqual(chunk[0]["active_ms"], 10000)
        self.assertIsNotNone(chunk[0]["ts"])
        # continuing the SAME app must NOT emit a broken event — a fresh segment opens
        more = seg.feed(mk("code", 0), 5)
        self.assertEqual(more, [])
        # the post-checkpoint segment closes cleanly on the next boundary
        e = seg.feed(mk("chrome", 0, "github.com"), 5)
        self.assertEqual(len(e), 1)
        self.assertEqual(e[0]["sub_category"], "dev_tools")
        self.assertEqual(e[0]["active_ms"], 5000)   # only the post-checkpoint 5s
        self.assertIsNotNone(e[0]["ts"])

    def test_counts_only_never_content(self):
        seg = Segmenter(idle_gap_s=180)
        seg.feed(mk("code", 0), 5)
        ev = seg.flush()[0]
        # event carries counts but no field that could hold typed text
        self.assertEqual(ev["key_count"], 1)
        self.assertNotIn("keystrokes", ev)
        self.assertNotIn("text", ev)


if __name__ == "__main__":
    unittest.main()
