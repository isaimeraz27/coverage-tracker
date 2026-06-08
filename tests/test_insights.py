"""Insights engine — canonical §3.9 worked example as the fixture, plus the
§6 active_s correction and a 'wasting time' detection check."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from insights import engine  # noqa: E402

H = 3600.0


class TestCanonicalExample(unittest.TestCase):
    """§3.9 jdoe/Ana developer day -> Adherence 85.5%, score 85.6 (FQ forced 0.80)."""

    def ledger(self):
        return engine.DayLedger(
            on_task_active_s=(4.0 + 0.5 + 0.4 + 0.6) * H,  # dev/review/docs/comms
            distract_active_s=0.8 * H,
            other_active_s=0.0,
            meeting_s=1.0 * H,
            idle_short_s=0.3 * H,
            idle_long_s=0.5 * H,
            key_count=15000, mouse_count=2000, mouse_px=300000,  # a normally-engaged day
        )

    def test_ledger_identities(self):
        L = self.ledger()
        self.assertAlmostEqual(L.active_s, 6.3 * H, places=3)     # §3.9
        self.assertAlmostEqual(L.present_s, 7.6 * H, places=3)
        self.assertAlmostEqual(L.on_task_s, 6.5 * H, places=3)

    def test_adherence_and_score(self):
        ins = engine.compute_day(self.ledger(), focus_quality_override=0.80)
        self.assertAlmostEqual(ins.adherence, 0.8553, places=3)
        self.assertAlmostEqual(ins.distract_ratio, 0.1053, places=3)
        self.assertEqual(ins.score, 85.6)                          # §3.9 canonical
        self.assertFalse(ins.attention)                            # a good day

    def test_engagement_does_not_move_score(self):
        # engagement is a SECONDARY signal: changing it must not change the score
        base = engine.compute_day(self.ledger(), focus_quality_override=0.80)
        L = self.ledger(); L.key_count = L.mouse_count = 0; L.mouse_px = 0
        low = engine.compute_day(L, focus_quality_override=0.80)
        self.assertEqual(base.score, low.score)                    # identical score
        self.assertGreater(base.engagement, low.engagement)        # different engagement


class TestActiveSecondsCorrection(unittest.TestCase):
    """§6 erratum: 900+600 productive + 180 distracting + 120 idle -> active_s=1680."""

    def test_active_s_1680(self):
        L = engine.DayLedger(on_task_active_s=1500, distract_active_s=180, idle_short_s=120)
        self.assertEqual(L.active_s, 1680)            # not 1080
        self.assertEqual(L.present_s, 1800)


class TestWastingTimeDetection(unittest.TestCase):
    def test_distracted_day_trips_attention(self):
        L = engine.DayLedger(
            on_task_active_s=1.5 * H, distract_active_s=3.5 * H, meeting_s=0.5 * H,
            idle_long_s=2.5 * H, idle_short_s=0.2 * H)
        ins = engine.compute_day(L)
        codes = {f.code for f in ins.flags}
        self.assertIn("distracting_excess", codes)
        self.assertIn("high_idle", codes)
        self.assertTrue(ins.attention)               # >=2 negative / high severity

    def test_thin_data_needs_context(self):
        L = engine.DayLedger(on_task_active_s=1.0 * H, meeting_s=0.3 * H, idle_short_s=0.1 * H)
        ins = engine.compute_day(L)
        self.assertTrue(ins.needs_context)           # §3.10 refuse to conclude
        self.assertLess(ins.data_completeness, 0.6)


class TestEngagementSignal(unittest.TestCase):
    """Cursor+click engagement: high active time but near-zero input -> an
    informational 'low_engagement' note that does NOT, by itself, trip attention."""

    def test_present_but_low_input(self):
        L = engine.DayLedger(on_task_active_s=5 * H)   # busy-looking, zero input
        ins = engine.compute_day(L)
        self.assertLess(ins.engagement, 0.2)
        self.assertIn("low_engagement", {f.code for f in ins.flags})
        self.assertFalse(ins.attention)                # info-only never trips the gate

    def test_active_and_typing_is_engaged(self):
        L = engine.DayLedger(on_task_active_s=5 * H, key_count=14000, mouse_count=1800, mouse_px=250000)
        ins = engine.compute_day(L)
        self.assertGreater(ins.engagement, 0.7)
        self.assertNotIn("low_engagement", {f.code for f in ins.flags})


if __name__ == "__main__":
    unittest.main()
