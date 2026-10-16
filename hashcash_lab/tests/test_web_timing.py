"""Separate web preparation, verified proof, and final cleanup clocks.

Only CPU fixtures run here. Native timestamps use the same deterministic QPC
domain as Python's Windows perf_counter, so delayed observation cannot masquerade
as a slower proof search.
"""

import tempfile
import unittest
from hashlib import sha256
from unittest.mock import patch

from hashcash_lab import server as lab
from hashcash_lab.history import HistoryStore


MESSAGE = "한글🙂"
NONCE = 980
PROOF = sha256(MESSAGE.encode("utf-8") + str(NONCE).encode("ascii")).hexdigest()


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


class WebTimingTests(unittest.TestCase):
    def job(self, **options):
        return lab.SearchJob(lab.SearchConfig(MESSAGE, 3, mode="gpu"), **options)

    def test_new_search_has_no_inherited_proof_timestamp(self):
        clock = Clock()
        with patch.object(lab.time, "perf_counter", clock):
            previous = self.job()
            clock.now = 102.0
            self.assertTrue(previous.accept({"nonce": NONCE, "hash": PROOF}))
            current = self.job()
        self.assertIsNone(current.snapshot()["proofElapsed"])
        self.assertIsNone(current.snapshot()["result"])
        self.assertNotEqual(previous.snapshot()["id"], current.snapshot()["id"])

    def test_default_proof_clock_is_sampled_after_independent_sha256(self):
        clock = Clock()

        def verify(data):
            # Verification itself is inside the measured web proof boundary.
            clock.now += 0.2
            return sha256(data)

        with patch.object(lab.time, "perf_counter", clock):
            job = self.job()
            job.update(startupElapsed=1.5)
            clock.now = 101.9
            with patch.object(lab, "sha256", side_effect=verify):
                self.assertTrue(job.accept({"nonce": NONCE, "hash": PROOF}))
            state = job.snapshot()
        self.assertEqual(state["status"], "found")
        self.assertAlmostEqual(state["proofElapsed"], 2.1)
        self.assertAlmostEqual(state["proofElapsed"] - state["startupElapsed"], 0.6)

    def test_absent_or_invalid_proof_never_publishes_a_found_time(self):
        with patch.object(lab.time, "perf_counter", Clock()):
            job = self.job()
            self.assertFalse(job.accept({"nonce": None, "hash": None}))
            with self.assertRaises(ValueError):
                job.accept({"nonce": NONCE, "hash": "0" * 64})
            self.assertIsNone(job.snapshot()["proofElapsed"])
            self.assertIsNone(job.snapshot()["result"])

    def test_explicit_native_time_is_validated_before_accepting_a_proof(self):
        for elapsed in (-1, float("nan"), float("inf"), True, "2.0", 1.4):
            with self.subTest(proof_elapsed=elapsed):
                with patch.object(lab.time, "perf_counter", Clock(103.0)):
                    job = self.job()
                    job.update(startupElapsed=1.5)
                    with self.assertRaises(ValueError):
                        job.accept({"nonce": NONCE, "hash": PROOF}, proof_elapsed=elapsed)
                    self.assertIsNone(job.snapshot()["proofElapsed"])
                    self.assertIsNone(job.snapshot()["result"])

    def test_first_verified_proof_keeps_its_timestamp_on_later_observation(self):
        clock = Clock()
        with patch.object(lab.time, "perf_counter", clock):
            job = self.job()
            job.update(startupElapsed=1.5)
            clock.now = 105.0
            self.assertTrue(job.accept({"nonce": NONCE, "hash": PROOF}, proof_elapsed=1.8))
            first = job.snapshot()
            clock.now = 106.0
            self.assertTrue(job.accept({"nonce": NONCE, "hash": PROOF}))
            later = job.snapshot()
        self.assertEqual(first["proofElapsed"], 1.8)
        self.assertEqual(later["proofElapsed"], first["proofElapsed"])
        self.assertEqual(later["result"], first["result"])

    def test_gpu_preparation_proof_and_cleanup_are_distinct(self):
        clock = Clock()

        class GPU:
            name = "Clock controlled GPU fixture"

            def scan_batch(self, _start, _count):
                clock.now = 101.9
                return {"checked": NONCE + 1, "nonce": NONCE, "hash": PROOF,
                        "last_nonce": NONCE, "last_hash": PROOF}

            def close(self):
                clock.now = 102.2

        def initialize(*_):
            clock.now = 101.5
            return GPU()

        with patch.object(lab.time, "perf_counter", clock):
            job = self.job(gpu_factory=initialize)
            job.run()
            state = job.snapshot()
            clock.now = 999.0
            later = job.snapshot()
        self.assertEqual(state["status"], "found", state)
        self.assertAlmostEqual(state["startupElapsed"], 1.5)
        self.assertAlmostEqual(state["proofElapsed"], 1.9)
        self.assertAlmostEqual(state["elapsed"], 2.2)
        self.assertAlmostEqual(state["proofElapsed"] - state["startupElapsed"], 0.4)
        for field in ("startupElapsed", "proofElapsed", "elapsed", "hashesPerSecond"):
            self.assertEqual(later[field], state[field], field)

    def test_native_proof_uses_c_timestamp_instead_of_progress_poll_or_drain(self):
        clock = Clock()

        class GPU:
            name = "Unused native GPU fixture"

            def scan_batch(self, *_):
                raise AssertionError("C mode entered the Python GPU loop")

            def close(self):
                clock.now = 103.2

        class Native:
            def __init__(self, *_args, **_options):
                self.value = {}

            def attach_gpu(self, _gpu):
                clock.now = 101.5

            def reset(self, _start, _count, **_options):
                self.value = {"cpu_checked": 0, "gpu_checked": 0, "checked": 0,
                              "nonce": None, "begin": 0.0, "first_proof_seconds": 0.0}

            def stats(self):
                return dict(self.value)

            def run(self):
                # C found and verified its proof at 101.80. The next Python
                # observer/final return arrives after in-flight work drains.
                clock.now = 103.0
                self.value = {"cpu_checked": 100, "gpu_checked": NONCE + 1,
                              "checked": NONCE + 101, "nonce": str(NONCE), "hash": PROOF,
                              "begin": 101.55, "first_proof_seconds": 0.25,
                              "status": 0, "qpc_frequency": 10_000_000.0}
                return self.stats()

            def cancel(self):
                pass

            def traces(self):
                return []

            def close(self):
                clock.now = 103.1

        with patch.object(lab.time, "perf_counter", clock):
            job = lab.SearchJob(lab.SearchConfig(MESSAGE, 3, mode="native"),
                                gpu_factory=lambda *_: GPU(), native_factory=Native)
            job.run()
            state = job.snapshot()
        self.assertEqual(state["status"], "found", state)
        self.assertTrue(state["result"]["verified"])
        self.assertAlmostEqual(state["startupElapsed"], 1.5)
        self.assertAlmostEqual(state["proofElapsed"], 1.8)
        self.assertAlmostEqual(state["proofElapsed"] - state["startupElapsed"], 0.3)
        self.assertAlmostEqual(state["elapsed"], 3.2)

    def test_gpu_digit_kernel_preparation_is_removed_from_active_proof_search(self):
        clock = Clock()

        class GPU:
            name = "Kernel preparation timing fixture"
            search_preparation_seconds = 0.0
            calls = 0

            def scan_batch(self, _start, _count):
                # The first decimal-width specialization costs 250 ms after
                # startup. The proof then costs another 150 ms of real search.
                # The cumulative value repeats on another batch; it must not
                # be added twice to the preparation duration.
                self.calls += 1
                self.search_preparation_seconds = 0.25
                if self.calls == 1:
                    clock.now = 101.85
                    return {"checked": 800, "nonce": None, "hash": None,
                            "last_nonce": 799,
                            "last_hash": sha256(MESSAGE.encode("utf-8") + b"799").hexdigest()}
                clock.now = 101.9
                return {"checked": NONCE - 799, "nonce": NONCE, "hash": PROOF,
                        "last_nonce": NONCE, "last_hash": PROOF}

        def initialize(*_):
            clock.now = 101.5
            return GPU()

        with patch.object(lab.time, "perf_counter", clock):
            job = self.job(gpu_factory=initialize)
            preparing = job.snapshot()
            self.assertIsNone(preparing["preparationElapsed"])
            self.assertIsNone(preparing["proofSearchElapsed"])
            job.run()
            state = job.snapshot()
        self.assertEqual(state["status"], "found", state)
        self.assertAlmostEqual(state["startupElapsed"], 1.5)
        self.assertAlmostEqual(state["searchPreparationElapsed"], 0.25)
        self.assertAlmostEqual(state["preparationElapsed"], 1.75)
        self.assertAlmostEqual(state["proofElapsed"], 1.9)
        self.assertAlmostEqual(state["proofSearchElapsed"], 0.15)

    def test_persistence_retry_does_not_change_terminal_timing(self):
        clock = Clock()

        class KnownProofJob(lab.SearchJob):
            def run_cpu(self, _base):
                self.update(backend="CPU timing fixture", startupElapsed=1.5)
                clock.now = 101.9
                self.record({"checked": NONCE + 1, "last_nonce": NONCE, "last_hash": PROOF})
                self.accept({"nonce": NONCE, "hash": PROOF})
                clock.now = 102.2

        with tempfile.TemporaryDirectory() as directory:
            history = HistoryStore(directory)
            with patch.object(lab.time, "perf_counter", clock):
                job = KnownProofJob(lab.SearchConfig(MESSAGE, 3, mode="cpu"), history=history)
                job.run()
                first = job.snapshot()
                clock.now = 999.0
                later = job.save_result()
            self.assertEqual(first["status"], "found", first)
            self.assertIsNotNone(first["recordId"])
            for field in ("recordId", "startupElapsed", "proofElapsed", "elapsed", "hashesPerSecond"):
                self.assertEqual(later[field], first[field], field)
            rows = history.query()
            self.assertEqual(rows["total"], 1)
            self.assertAlmostEqual(rows["records"][0]["elapsed"], 2.2)


if __name__ == "__main__":
    unittest.main()
