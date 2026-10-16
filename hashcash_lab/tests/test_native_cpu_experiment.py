"""Small independent native vectors. No throughput measurement or GPU import."""
import multiprocessing as mp
import unittest
from hashlib import sha256

from hashcash_lab.cpu import MAX_NONCE, scan_batch
from hashcash_lab.native_cpu_experiment import (scan_native_batch, selected_dll,
                                               timed_native_scan)


@unittest.skipUnless(selected_dll().is_file(), "Optional native experiment has not been built")
class NativeCpuVectorTests(unittest.TestCase):
    def test_exact_bytes_decimal_boundaries_and_uint64(self):
        messages = ("", " x\n", "한글🙂\n", "a\x00b", "x" * 55, "x" * 56,
                    "x" * 63, "x" * 64, "🙂" * 40)
        for message in messages:
            for start, count in ((0, 2), (8, 4), (98, 4), (998, 4),
                                 (2 ** 53 + 1, 3), (10 ** 19 - 2, 4), (MAX_NONCE - 2, 3)):
                with self.subTest(message=ascii(message), start=start):
                    actual = scan_native_batch(message, 64, start, count, search=False)
                    expected = scan_batch(message, 64, start, count, search=False)
                    self.assertEqual(actual, expected)
                    independent = sha256(message.encode("utf-8") + str(start + count - 1).encode("ascii")).hexdigest()
                    self.assertEqual(actual["last_hash"], independent)

    def test_search_stops_at_first_proof_and_fixed_work_continues(self):
        for message in ("", " x\n", "한글🙂"):
            for difficulty in (1, 2, 3):
                # Derive the expected first proof independently, including a
                # known one near 980 for the Unicode difficulty-3 case.
                end = 1100
                hashes = [(nonce, sha256(message.encode("utf-8") + str(nonce).encode("ascii")).hexdigest())
                          for nonce in range(end)]
                proofs = [(nonce, value) for nonce, value in hashes if value.startswith("0" * difficulty)]
                for search in (False, True):
                    with self.subTest(message=ascii(message), difficulty=difficulty, search=search):
                        actual = scan_native_batch(message, difficulty, 0, end, search=search)
                        self.assertEqual(actual["nonce"], proofs[0][0] if proofs else None)
                        self.assertEqual(actual["hash"], proofs[0][1] if proofs else None)
                        self.assertEqual(actual["checked"], proofs[0][0] + 1 if search and proofs else end)
                        self.assertEqual(actual["last_hash"], hashes[actual["checked"] - 1][1])

    def test_all_difficulty_predicates_match_independent_hex(self):
        message, nonce = "한글🙂", 980
        value = sha256(message.encode("utf-8") + b"980").hexdigest()
        for difficulty in range(1, 65):
            actual = scan_native_batch(message, difficulty, nonce, 1)
            self.assertEqual(actual["nonce"] is not None, value.startswith("0" * difficulty))

    def test_invalid_inputs_do_not_wrap_or_coerce(self):
        invalid = [("x", 0, 0, 1), ("x", 65, 0, 1), ("x", True, 0, 1),
                   ("x", 1, -1, 1), ("x", 1, MAX_NONCE + 1, 1),
                   ("x", 1, MAX_NONCE, 2), ("x", 1, 0, 0),
                   ("x", 1, 0, True), ("x", 1, 0, MAX_NONCE + 1),
                   (b"x", 1, 0, 1), ("\ud800", 1, 0, 1)]
        for args in invalid:
            with self.subTest(args=ascii(args)):
                with self.assertRaises(ValueError):
                    scan_native_batch(*args)
        with self.assertRaises(ValueError):
            scan_native_batch("x", 1, 0, 1, search=1)

    def test_spawn_entry_point_uses_selected_cached_dll(self):
        with mp.get_context("spawn").Pool(1) as pool:
            batch, began, ended = pool.apply(timed_native_scan, ("a\x00한글🙂\n", 64, MAX_NONCE - 2, 3, False))
        self.assertEqual(batch, scan_batch("a\x00한글🙂\n", 64, MAX_NONCE - 2, 3, search=False))
        self.assertLessEqual(began, ended)


if __name__ == "__main__":
    unittest.main()
