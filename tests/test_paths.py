"""agent.paths resolves the install/data dir correctly in BOTH source and frozen modes.

CAUTION: sys.frozen / sys.executable are process-global; faking them here MUST be restored
in a finally block or it poisons every other test in the run."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agent import paths  # noqa: E402


class TestPaths(unittest.TestCase):
    def test_source_mode_is_repo_root(self):
        self.assertFalse(getattr(sys, "frozen", False))
        self.assertTrue(paths.install_dir().endswith("employee-tracker"))
        self.assertEqual(paths.data_path("config.json"),
                         os.path.join(paths.install_dir(), "config.json"))

    def test_frozen_mode_is_exe_dir(self):
        real_exe = sys.executable
        try:
            sys.frozen = True
            fake = os.path.join(os.sep, "fake", "CoverageAgent", "coverage-agent.exe")
            sys.executable = fake
            self.assertEqual(paths.install_dir(), os.path.dirname(fake))
            self.assertEqual(paths.data_path("agent_token.txt"),
                             os.path.join(os.path.dirname(fake), "agent_token.txt"))
        finally:
            sys.executable = real_exe          # restore — must not leak
            if hasattr(sys, "frozen"):
                del sys.frozen
        # confirm we're cleanly back in source mode for the rest of the suite
        self.assertFalse(getattr(sys, "frozen", False))

    def test_version_string_present(self):
        self.assertIsInstance(paths.AGENT_VERSION, str)
        self.assertTrue(paths.AGENT_VERSION)


if __name__ == "__main__":
    unittest.main()
