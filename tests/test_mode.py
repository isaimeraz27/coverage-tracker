"""present_insight() is the single verdict gate: coaching mode (or any uncalibrated role)
must never emit a target/verdict; evaluative + calibrated must emit verdict == score>=target.
This is the "coaching until calibration" guarantee, tested without HTTP."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from server import db, api  # noqa: E402
from insights import engine  # noqa: E402


def _insight(score):
    # a minimal, plausible DayInsight; only `score`/`attention`/`needs_context`/flags are read
    return engine.DayInsight(
        score=score, adherence=0.8, distract_ratio=0.1, focus_quality=0.8,
        present_s=7 * 3600, active_s=6 * 3600, on_task_s=5 * 3600, meeting_s=3600,
        idle_long_s=600, data_completeness=0.95, confidence=0.95, needs_context=False,
        role_target_score=70.0, engagement=0.6, attention=False, flags=[],
    )


class TestPresentInsight(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.conn = db.connect(self.path)
        db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def _present(self, mode, calibrated, target, score=86.0):
        db.set_setting(self.conn, "mode", mode)
        ins = _insight(score)
        extra = {"calibrated": calibrated, "target_score": target, "top": []}
        return api.present_insight(self.conn, ins, extra)

    def test_coaching_never_emits_verdict(self):
        d = self._present("coaching", calibrated=True, target=70.0)
        self.assertIsNone(d["verdict"])
        self.assertIsNone(d["target"])
        self.assertIsNotNone(d["coaching"])
        self.assertEqual(d["mode"], "coaching")

    def test_uncalibrated_forces_coaching_even_in_evaluative(self):
        d = self._present("evaluative", calibrated=False, target=None)
        self.assertIsNone(d["verdict"])
        self.assertIsNone(d["target"])
        self.assertIsNotNone(d["coaching"])

    def test_evaluative_calibrated_pass(self):
        d = self._present("evaluative", calibrated=True, target=70.0, score=86.0)
        self.assertEqual(d["target"], 70.0)
        self.assertEqual(d["verdict"], "pass")
        self.assertIsNone(d["coaching"])

    def test_evaluative_calibrated_fail(self):
        d = self._present("evaluative", calibrated=True, target=90.0, score=86.0)
        self.assertEqual(d["verdict"], "fail")

    def test_score_present_in_all_modes(self):
        for mode, cal, tgt in (("coaching", True, 70.0), ("evaluative", False, None),
                               ("evaluative", True, 70.0)):
            d = self._present(mode, cal, tgt, score=86.0)
            self.assertEqual(d["score"], 86.0)  # score is always present (trend value)


if __name__ == "__main__":
    unittest.main()
