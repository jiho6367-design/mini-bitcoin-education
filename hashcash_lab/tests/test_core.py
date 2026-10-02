"""Reference proofs and range coverage, independent of backend timing."""

import random
import unittest
from hashlib import sha256

from hashcash_lab.cpu import (
    MAX_NONCE,
    meets_difficulty,
    proof_hash,
    scan_batch,
    validate_difficulty,
)


REFERENCE_PROOFS = (
    (
        "Pyeongtaek University!", 3, 8656,
        "00028e62333a32b5b6bdf41418f42725f8c21d84dc0b7ff60e4480cc11a8677b",
    ),
    (
        "You are the best of best!!!!", 3, 3055,
        "0002a0cc7ab05331087b7525cea18a492af097a20f4c350f5b83bd7ce05f8010",
    ),
    (
        "\ud55c\uae00\U0001f642", 3, 980,
        "00091f64964396f15d07238d0d352913789e1dea831702392b3ceb73d6ac99ad",
    ),
    (
        "", 3, 886,
        "000f21ac06aceb9cdd0575e82d0d85fc39bed0a7a1d71970ba1641666a44f530",
    ),
)

# Proofs committed by the teacher in results_ultra.csv at the audited commit.
TEACHER_PROOFS = (
    (5, 1063959, "000005fba5772405a173d1f84c9ca674396248d7c7bdecd400b6901a2cb7e359"),
    (6, 18875536, "0000007adeb9cc7c0fe5e160d451c4c7d5e4c6a2376f04fb5eb702d9af33e8a5"),
    (7, 387520263, "0000000c83f1b55f479526ff1e2c8fb85ff24cdf474b7a63d3aa1454f1cf2f12"),
    (8, 3243428010, "000000002414c5ac2781b41a79da1a3cda116fd3133f9767d362a169f45c1efa"),
    (9, 31265275765, "00000000011a8ce3afc08afdebbad1a7db366584e305cd157b8b02c664d85566"),
    (10, 654147500349, "0000000000f664d568eeac3a95562bde2e593d03781308e860396ad9c9986264"),
    (11, 19890660490867, "00000000000520a02a39be5775977c8c6b9ac4ebd88ea56d888842f09b4f4dd8"),
    (12, 276030169824427, "000000000000649e6501c617448d2b711f6e50d6b15f1e76154efc12cc776fc1"),
    (13, 1016822103601942, "00000000000001b65445893822a760f06ce46d76804d216fe13712d433660f7f"),
)


class HashcashCoreTests(unittest.TestCase):
    def test_reference_proofs_match_teacher_encoding(self):
        for message, difficulty, nonce, expected in REFERENCE_PROOFS:
            with self.subTest(message=ascii(message), nonce=nonce):
                self.assertEqual(proof_hash(message, nonce), expected)
                self.assertTrue(meets_difficulty(bytes.fromhex(expected), difficulty))

    def test_teacher_recorded_proofs_match_independent_hashlib(self):
        message = "You are the best of best!!!!"
        for difficulty, nonce, expected in TEACHER_PROOFS:
            with self.subTest(difficulty=difficulty):
                independent = sha256(message.encode("utf-8") + str(nonce).encode("ascii")).hexdigest()
                self.assertEqual(independent, expected)
                self.assertEqual(proof_hash(message, nonce), expected)
                self.assertTrue(meets_difficulty(bytes.fromhex(expected), difficulty))

    def test_actual_cpu_scan_has_no_nonce_gap_before_reference_proof(self):
        for message, difficulty, nonce, expected in REFERENCE_PROOFS:
            with self.subTest(message=ascii(message)):
                before = scan_batch(message, difficulty, 0, nonce)
                self.assertEqual(before["checked"], nonce)
                self.assertIsNone(before["nonce"])
                self.assertIsNone(before["hash"])
                self.assertEqual(before["last_nonce"], nonce - 1)
                at_proof = scan_batch(message, difficulty, nonce, 1)
                self.assertEqual(at_proof["checked"], 1)
                self.assertEqual(at_proof["nonce"], nonce)
                self.assertEqual(at_proof["hash"], expected)

    def test_early_stop_and_full_count_have_distinct_correct_counts(self):
        message, difficulty, nonce, expected = REFERENCE_PROOFS[2]
        early = scan_batch(message, difficulty, 0, nonce + 5)
        self.assertEqual(early["checked"], nonce + 1)
        self.assertEqual(early["nonce"], nonce)
        self.assertEqual(early["hash"], expected)
        full = scan_batch(message, difficulty, 0, nonce + 5, search=False)
        self.assertEqual(full["checked"], nonce + 5)
        self.assertEqual(full["nonce"], nonce)
        self.assertEqual(full["hash"], expected)
        self.assertEqual(full["last_nonce"], nonce + 4)
        self.assertEqual(full["last_hash"], proof_hash(message, nonce + 4))

    def test_stepped_scan_covers_the_submitted_arithmetic_progression(self):
        message, difficulty, nonce, expected = REFERENCE_PROOFS[0]
        result = scan_batch(message, difficulty, nonce - 3, 2, step=3)
        self.assertEqual(result["checked"], 2)
        self.assertEqual(result["nonce"], nonce)
        self.assertEqual(result["hash"], expected)

    def test_utf8_message_is_exact_and_not_trimmed(self):
        messages = ("", " ", " x\n", "x\n", "\ud55c\uae00\U0001f642\n", "a\x00b")
        nonces = (0, 9, 10, 99, 100, 2 ** 53 + 1, MAX_NONCE)
        for message in messages:
            for nonce in nonces:
                with self.subTest(message=ascii(message), nonce=nonce):
                    expected = sha256(message.encode("utf-8") + str(nonce).encode("ascii")).hexdigest()
                    self.assertEqual(proof_hash(message, nonce), expected)
        self.assertNotEqual(proof_hash(" x\n", 0), proof_hash("x", 0))

    def test_predicate_matches_hex_reference_for_all_64_difficulties(self):
        rng = random.Random(9102)
        digests = [rng.randbytes(32) for _ in range(128)]
        digests += [b"\x00" * 32, b"\x0f" + b"\xff" * 31]
        digests += [b"\x00" * size + b"\x0f" + b"\xff" * (31 - size) for size in range(32)]
        for difficulty in range(1, 65):
            for digest in digests:
                expected = digest.hex().startswith("0" * difficulty)
                self.assertEqual(meets_difficulty(digest, difficulty), expected,
                                 (difficulty, digest.hex()))

    def test_complete_sha256_digest_can_satisfy_difficulty_64(self):
        self.assertTrue(meets_difficulty(b"\x00" * 32, 64))
        self.assertTrue(meets_difficulty(b"\x00" * 31 + b"\x0f", 63))
        self.assertFalse(meets_difficulty(b"\x00" * 31 + b"\x0f", 64))

    def test_nonce_domain_end_does_not_wrap(self):
        result = scan_batch("end", 64, MAX_NONCE - 2, 3, search=False)
        self.assertEqual(result["checked"], 3)
        self.assertEqual(result["last_nonce"], MAX_NONCE)
        self.assertEqual(result["last_hash"], proof_hash("end", MAX_NONCE))
        for start, count, step in ((MAX_NONCE, 2, 1), (MAX_NONCE - 1, 2, 2)):
            with self.subTest(start=start, count=count, step=step):
                with self.assertRaises(ValueError):
                    scan_batch("end", 64, start, count, step=step)

    def test_invalid_inputs_are_controlled_errors(self):
        for difficulty in (0, 65, -1, True, 1.5, "3", None):
            with self.subTest(difficulty=difficulty):
                with self.assertRaises(ValueError):
                    validate_difficulty(difficulty)
        for nonce in (-1, MAX_NONCE + 1, True, 0.5, "1", None):
            with self.subTest(nonce=nonce):
                with self.assertRaises(ValueError):
                    proof_hash("message", nonce)
        for message in (None, b"message", "\ud800"):
            with self.subTest(message=ascii(message)):
                with self.assertRaises(ValueError):
                    proof_hash(message, 0)
        for count in (0, -1, True, 1.5):
            with self.subTest(count=count):
                with self.assertRaises(ValueError):
                    scan_batch("message", 3, 0, count)
        for step in (0, -1, True, 1.5):
            with self.subTest(step=step):
                with self.assertRaises(ValueError):
                    scan_batch("message", 3, 0, 1, step=step)
        for digest in (b"", b"\x00" * 31, b"\x00" * 33):
            with self.subTest(length=len(digest)):
                with self.assertRaises(ValueError):
                    meets_difficulty(digest, 1)


if __name__ == "__main__":
    unittest.main()
