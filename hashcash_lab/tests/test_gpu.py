"""Pure CUDA-source checks plus explicitly opted-in hardware integration.

The default suite never imports CuPy. To execute CUDA tests on an environment
with an allowed CUDA/CuPy installation, set HASHCASH_TEST_GPU=1. A failed GPU
initialization then fails those tests rather than silently marking them passed.
"""

import hashlib
import os
import re
import unittest
from unittest.mock import patch

from hashcash_lab.gpu import (
    CudaBackend, GpuUnavailable, UINT64_MAX,
    _build_cuda, _word_layout, batch_count,
)


class GpuSourceTests(unittest.TestCase):
    def test_padding_and_nonce_slots_at_word_and_utf8_boundaries(self):
        messages = [b"x" * size for size in (0, 1, 3, 4, 31, 32, 33, 34, 35)]
        messages += [("가" * 11).encode("utf-8"), ("가" * 11 + "ab").encode("utf-8")]
        nonces = (0, 9, 10, 99, 100, 1 << 32, 10 ** 19 - 1, 10 ** 19, UINT64_MAX)
        for message in messages:
            for nonce in nonces:
                with self.subTest(bytes=len(message), nonce=nonce):
                    suffix = str(nonce).encode("ascii")
                    words, slots = _word_layout(message, len(suffix))
                    for value, (word, shift) in zip(suffix, slots):
                        words[word] |= value << shift
                    actual = b"".join(word.to_bytes(4, "big") for word in words)
                    payload = message + suffix
                    expected = (payload + b"\x80" + b"\x00" * (55 - len(payload))
                                + (len(payload) * 8).to_bytes(8, "big"))
                    self.assertEqual(actual, expected)
                    self.assertEqual(len(actual), 64)

    def test_layout_rejects_unsupported_padding_or_nonce_length(self):
        for message, digits in ((b"", 0), (b"", 21), (b"x" * 36, 20), (b"x" * 55, 1)):
            with self.subTest(bytes=len(message), digits=digits):
                with self.assertRaises(ValueError):
                    _word_layout(message, digits)

    def test_nonce_batches_do_not_cross_decimal_or_uint64_bounds(self):
        cases = ((0, 100, 10), (9, 100, 1), (10, 1000, 90), (99, 9, 1),
                 (100, 1000, 900), (10 ** 19 - 1, 100, 1),
                 (UINT64_MAX - 16, 100, 17), (UINT64_MAX, 100, 1))
        for start, count, expected in cases:
            with self.subTest(start=start, count=count):
                self.assertEqual(batch_count(start, count), expected)

    def test_decimal_boundary_splits_cover_exact_requested_range(self):
        for first, requested in ((0, 1003), (98, 107), (10 ** 19 - 3, 7), (UINT64_MAX - 6, 7)):
            with self.subTest(start=first, count=requested):
                current = first
                remaining = requested
                spans = []
                while remaining:
                    checked = batch_count(current, remaining)
                    self.assertGreater(checked, 0)
                    self.assertEqual(len(str(current)), len(str(current + checked - 1)))
                    spans.append((current, current + checked))
                    current += checked
                    remaining -= checked
                self.assertEqual(spans[0][0], first)
                self.assertEqual(spans[-1][1], first + requested)
                for before, after in zip(spans, spans[1:]):
                    self.assertEqual(before[1], after[0])

    def test_bad_nonce_and_count_types_are_rejected(self):
        for start in (-1, UINT64_MAX + 1, 1.0, "1", True):
            with self.subTest(start=start):
                with self.assertRaises(ValueError):
                    batch_count(start, 1)
        for count in (0, -1, 1 << 32, 1.0, "1", True):
            with self.subTest(count=count):
                with self.assertRaises(ValueError):
                    batch_count(0, count)

    def test_generated_target_masks_cover_partial_and_whole_hash_words(self):
        # Expected hexadecimal masks are explicit boundary cases, independent
        # of the generator's division/remainder calculation.
        expected = {1: (1, "0xf0000000u"), 7: (1, "0xfffffff0u"),
                    8: (1, None), 9: (2, "0xf0000000u"),
                    63: (8, "0xfffffff0u"), 64: (8, None)}
        for difficulty, (comparisons, mask) in expected.items():
            with self.subTest(difficulty=difficulty):
                source = _build_cuda(b"test", difficulty, 20, 16)
                target = re.search(r"        if \((.*?)\) \{\n            if \(atomicCAS", source, re.DOTALL)
                self.assertIsNotNone(target)
                predicate = target.group(1)
                self.assertEqual(predicate.count(" == 0u"), comparisons)
                if mask is None:
                    self.assertNotIn(" & ", predicate)
                else:
                    self.assertIn(mask, predicate)
                self.assertIn("*found_nonce = thread_first + trial;", source)
                self.assertEqual(source.count("found_digest["), 8)

    def test_generator_validates_target_and_work_size(self):
        for difficulty in (0, 65):
            with self.assertRaises(ValueError):
                _build_cuda(b"", difficulty, 1, 16)
        for work in (0, 65537):
            with self.assertRaises(ValueError):
                _build_cuda(b"", 1, 1, work)

    def test_long_utf8_message_falls_back_before_optional_import(self):
        with patch.dict("sys.modules", {"cupy": None}):
            with self.assertRaisesRegex(GpuUnavailable, "35바이트"):
                CudaBackend("가" * 12, 3)

    def test_missing_optional_import_has_clear_fallback_exception(self):
        with patch.dict("sys.modules", {"cupy": None}):
            with self.assertRaisesRegex(GpuUnavailable, "CUDA 초기화/자체 검증 실패"):
                CudaBackend("test", 3)

    def test_runtime_search_selftest_cpu_vectors_are_positive_and_negative(self):
        hashes = [hashlib.sha256(b"CUDA proof self-test" + str(nonce).encode("ascii")).hexdigest()
                  for nonce in range(1000, 1512)]
        self.assertTrue(any(value.startswith("0") for value in hashes))
        self.assertFalse(any(value == "0" * 64 for value in hashes))


@unittest.skipUnless(os.environ.get("HASHCASH_TEST_GPU") == "1", "set HASHCASH_TEST_GPU=1 for real CUDA tests")
class CudaHardwareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.message = "해시캐시 🔐"
        cls.backend = CudaBackend(cls.message, 1)
        cls.negative = CudaBackend(cls.message, 64)

    def test_initialization_runs_digest_and_search_path_selftests(self):
        self.assertTrue(self.backend.self_test_passed)
        self.assertTrue(self.negative.self_test_passed)
        self.assertTrue(self.backend.name)

    def test_33_34_35_byte_messages_and_known_nonce_digests(self):
        for message in ("x" * 33, "x" * 34, "가" * 11 + "ab"):
            with self.subTest(bytes=len(message.encode("utf-8"))):
                backend = CudaBackend(message, 1)
                self.assertTrue(backend.self_test_passed)
                for nonce in (0, 9, 10, 99, 100, 1 << 32, UINT64_MAX):
                    digest = backend._dump_range(nonce, 1)[0]
                    expected = hashlib.sha256(message.encode("utf-8") + str(nonce).encode("ascii")).hexdigest()
                    self.assertEqual(digest, expected)

    def test_real_scan_returns_independently_validated_proof(self):
        result = self.backend.scan_batch(1000, 512)
        self.assertEqual(result["checked"], 512)
        self.assertIsNotNone(result["nonce"])
        self.assertTrue(1000 <= result["nonce"] < 1512)
        expected = hashlib.sha256(self.message.encode("utf-8") + str(result["nonce"]).encode("ascii")).hexdigest()
        self.assertEqual(result["hash"], expected)
        self.assertTrue(expected.startswith("0"))
        self.assertEqual(result["last_nonce"], 1511)

    def test_shared_result_buffer_clears_a_previous_proof(self):
        payload = self.message.encode("utf-8")
        hashes = {nonce: hashlib.sha256(payload + str(nonce).encode("ascii")).hexdigest()
                  for nonce in range(1000, 1512)}
        positive = next(nonce for nonce, digest in hashes.items() if digest.startswith("0"))
        negative = next(nonce for nonce, digest in hashes.items() if not digest.startswith("0"))
        found = self.backend.scan_batch(positive, 1)
        self.assertEqual(found["nonce"], positive)
        self.assertEqual(found["hash"], hashes[positive])
        missing = self.backend.scan_batch(negative, 1)
        self.assertIsNone(missing["nonce"])
        self.assertIsNone(missing["hash"])
        self.assertEqual(missing["last_hash"], hashes[negative])
        # Reusing the same pinned memory and event pair must still allow the
        # next successful launch to return fresh output.
        again = self.backend.scan_batch(positive, 1)
        self.assertEqual(again["nonce"], positive)
        self.assertEqual(again["hash"], hashes[positive])

    def test_shared_result_buffer_preserves_both_uint64_nonce_words(self):
        first, count = UINT64_MAX - 1023, 1024
        result = self.backend.scan_batch(first, count)
        self.assertEqual(result["checked"], count)
        self.assertIsNotNone(result["nonce"])
        self.assertGreaterEqual(result["nonce"], first)
        self.assertLessEqual(result["nonce"], UINT64_MAX)
        reference = hashlib.sha256(self.message.encode("utf-8")
                                   + str(result["nonce"]).encode("ascii")).hexdigest()
        self.assertEqual(result["hash"], reference)
        self.assertTrue(reference.startswith("0"))

    def test_negative_scan_and_uint64_end_do_not_wrap(self):
        result = self.negative.scan_batch(UINT64_MAX, 128)
        self.assertEqual(result["checked"], 1)
        self.assertIsNone(result["nonce"])
        self.assertIsNone(result["hash"])
        self.assertEqual(result["last_nonce"], UINT64_MAX)
        expected = hashlib.sha256(self.message.encode("utf-8") + str(UINT64_MAX).encode("ascii")).hexdigest()
        self.assertEqual(result["last_hash"], expected)

    def test_scan_clamps_at_decimal_length_then_resumes(self):
        first = self.negative.scan_batch(99, 4)
        self.assertEqual(first["checked"], 1)
        next_start = 99 + first["checked"]
        second = self.negative.scan_batch(next_start, 3)
        self.assertEqual(second["checked"], 3)
        self.assertEqual(second["last_nonce"], 102)


if __name__ == "__main__":
    unittest.main()
