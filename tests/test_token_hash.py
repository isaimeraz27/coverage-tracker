"""Tests for sha256 token hashing at rest (§3a production hardening).

Coverage:
  a. After enroll, machine.token stores hash_token(raw), not the raw value.
  b. verify_agent_token(conn, raw) resolves the correct machine id (normal path).
  c. A tampered/wrong token returns None.
  d. Legacy self-upgrade: a plaintext token row is accepted AND upgraded to hash in place.
  e. A revoked machine with the correct hash still fails verification.
  f. A stored hash, presented as a bearer token, is rejected (no replay via legacy path).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import db, auth  # noqa: E402


def _fresh_conn():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = db.connect(path)
    db.init_db(conn)
    return conn, path


class TestTokenHashAtRest(unittest.TestCase):

    def setUp(self):
        self.conn, self.path = _fresh_conn()

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    # ------------------------------------------------------------------ #
    # (a) Stored value is the sha256 hash, not the raw token              #
    # ------------------------------------------------------------------ #
    def test_a_stored_value_is_hash_not_raw(self):
        code = auth.issue_enrollment_code(self.conn, machine_id=None, label="test-a")
        raw = auth.enroll(self.conn, code, hostname="host-a")

        self.assertIsNotNone(raw)
        self.assertTrue(raw.startswith("eat_"))

        expected_hash = auth.hash_token(raw)

        # The DB must NOT contain the raw token.
        row_raw = self.conn.execute(
            "SELECT token FROM machine WHERE token=?", (raw,)
        ).fetchone()
        self.assertIsNone(row_raw, "Raw token must NOT appear in machine.token")

        # The DB must contain exactly the sha256 hex digest.
        row_hash = self.conn.execute(
            "SELECT token FROM machine WHERE token=?", (expected_hash,)
        ).fetchone()
        self.assertIsNotNone(row_hash, "sha256 hash must be stored in machine.token")
        stored = row_hash["token"]
        self.assertEqual(stored, expected_hash)
        self.assertEqual(len(stored), 64)
        self.assertNotEqual(stored, raw)

    # ------------------------------------------------------------------ #
    # (b) verify_agent_token resolves correctly on the normal (hashed) path
    # ------------------------------------------------------------------ #
    def test_b_verify_returns_correct_machine_id(self):
        code = auth.issue_enrollment_code(self.conn, machine_id=None, label="test-b")
        raw = auth.enroll(self.conn, code, hostname="host-b")

        machine_id = auth.verify_agent_token(self.conn, raw)
        self.assertIsNotNone(machine_id)
        self.assertIsInstance(machine_id, int)

        # Confirm it maps to the right machine row.
        row = self.conn.execute(
            "SELECT machine_id FROM machine WHERE id=?", (machine_id,)
        ).fetchone()
        self.assertIsNotNone(row)

    # ------------------------------------------------------------------ #
    # (c) Tampered / wrong token returns None                              #
    # ------------------------------------------------------------------ #
    def test_c_tampered_token_returns_none(self):
        code = auth.issue_enrollment_code(self.conn, machine_id=None, label="test-c")
        raw = auth.enroll(self.conn, code, hostname="host-c")

        self.assertIsNone(auth.verify_agent_token(self.conn, raw + "x"))
        self.assertIsNone(auth.verify_agent_token(self.conn, "eat_totally_wrong"))
        self.assertIsNone(auth.verify_agent_token(self.conn, ""))
        self.assertIsNone(auth.verify_agent_token(self.conn, "a" * 64))  # random hex

    # ------------------------------------------------------------------ #
    # (d) Legacy self-upgrade: plaintext row is accepted and upgraded      #
    # ------------------------------------------------------------------ #
    def test_d_legacy_plaintext_row_is_accepted_and_upgraded(self):
        # Simulate a pre-change enrollment: write a raw token directly as the
        # stored value (as the old code would have done).
        code = auth.issue_enrollment_code(self.conn, machine_id=None, label="test-d")
        raw = auth.enroll(self.conn, code, hostname="host-d")
        self.assertIsNotNone(raw)

        expected_hash = auth.hash_token(raw)

        # Force the stored value back to plaintext to simulate the old state.
        self.conn.execute(
            "UPDATE machine SET token=? WHERE token=?", (raw, expected_hash)
        )
        self.conn.commit()

        # Confirm the plaintext is now in the DB.
        row_before = self.conn.execute(
            "SELECT token FROM machine WHERE token=?", (raw,)
        ).fetchone()
        self.assertIsNotNone(row_before, "Setup: raw plaintext must be present before verify")

        # verify_agent_token must succeed (legacy path).
        machine_id = auth.verify_agent_token(self.conn, raw)
        self.assertIsNotNone(machine_id, "Legacy plaintext token must still resolve")

        # The row must now be upgraded to the hash (raw is gone).
        row_raw_after = self.conn.execute(
            "SELECT token FROM machine WHERE token=?", (raw,)
        ).fetchone()
        self.assertIsNone(row_raw_after, "Raw plaintext must be gone after self-upgrade")

        row_hash_after = self.conn.execute(
            "SELECT token FROM machine WHERE token=?", (expected_hash,)
        ).fetchone()
        self.assertIsNotNone(row_hash_after, "Hash must be stored after self-upgrade")

        # Second call (now hashed) must also succeed.
        machine_id2 = auth.verify_agent_token(self.conn, raw)
        self.assertEqual(machine_id, machine_id2)

    # ------------------------------------------------------------------ #
    # (e) Revoked machine fails even with the correct hash                 #
    # ------------------------------------------------------------------ #
    def test_e_revoked_machine_fails_verification(self):
        code = auth.issue_enrollment_code(self.conn, machine_id=None, label="test-e")
        raw = auth.enroll(self.conn, code, hostname="host-e")
        self.assertIsNotNone(raw)

        # Verify works before revocation.
        self.assertIsNotNone(auth.verify_agent_token(self.conn, raw))

        # Revoke the machine.
        expected_hash = auth.hash_token(raw)
        self.conn.execute(
            "UPDATE machine SET revoked=1 WHERE token=?", (expected_hash,)
        )
        self.conn.commit()

        # Verification must now fail.
        result = auth.verify_agent_token(self.conn, raw)
        self.assertIsNone(result, "Revoked machine must fail verify (normal/hash path)")

    def test_e_revoked_legacy_plaintext_fails_verification(self):
        """Revoked machine with a legacy plaintext token must also fail."""
        code = auth.issue_enrollment_code(self.conn, machine_id=None, label="test-e2")
        raw = auth.enroll(self.conn, code, hostname="host-e2")
        self.assertIsNotNone(raw)

        expected_hash = auth.hash_token(raw)

        # Put the plaintext back and mark it revoked — simulates old enrolled + revoked row.
        self.conn.execute(
            "UPDATE machine SET token=?, revoked=1 WHERE token=?", (raw, expected_hash)
        )
        self.conn.commit()

        result = auth.verify_agent_token(self.conn, raw)
        self.assertIsNone(result, "Revoked machine must fail verify (legacy path)")

    def test_f_stored_hash_presented_as_token_is_rejected(self):
        """The stored value is a hash; presenting it as a bearer token must NOT
        authenticate (else anyone who read the DB could impersonate the agent and
        corrupt the row via the legacy upgrade). The real raw token still works."""
        code = auth.issue_enrollment_code(self.conn, machine_id=None, label="test-f")
        raw = auth.enroll(self.conn, code, hostname="host-f")
        self.assertIsNotNone(raw)
        stored_hash = auth.hash_token(raw)

        # Present the stored hash as if it were the token — must be rejected.
        self.assertIsNone(
            auth.verify_agent_token(self.conn, stored_hash),
            "A stored hash must never be accepted as a bearer token",
        )
        # And the legitimate raw token must still resolve (row not corrupted).
        self.assertIsNotNone(auth.verify_agent_token(self.conn, raw))


if __name__ == "__main__":
    unittest.main()
