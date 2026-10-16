"""Small native scheduler correctness vectors; never a throughput benchmark."""
import os
import threading
import time
import unittest
from hashlib import sha256

from hashcash_lab.cpu import MAX_NONCE, scan_batch
from hashcash_lab.native_scheduler_experiment import (
    NativeRunError, NativeScheduler, selected_dll)


@unittest.skipUnless(selected_dll().is_file(), "Optional native scheduler has not been built")
class NativeSchedulerTests(unittest.TestCase):
    def assert_coverage(self, scheduler, start, count):
        traces = [trace for trace in scheduler.traces() if not trace["status"]]
        intervals = sorted((int(trace["start"]), trace["checked"]) for trace in traces)
        cursor = start
        for first, length in intervals:
            self.assertEqual(first, cursor)
            cursor += length
            self.assertGreater(length, 0)
        self.assertEqual(cursor, start + count)

    def test_exact_message_bytes_boundaries_uint64_and_reset(self):
        for message in ("", " x\n", "a\x00한글🙂\n", "x" * 64, "🙂" * 40):
            with NativeScheduler(message, 64, workers=2, cpu_batch=7, trace_capacity=128) as scheduler:
                self.assertEqual(scheduler.stats()["workers_ready"], 2)
                for start, count in ((0, 19), (98, 7), (998, 7),
                                     (2 ** 53 + 1, 13), (10 ** 19 - 2, 7), (MAX_NONCE - 16, 17)):
                    with self.subTest(message=ascii(message), start=start):
                        actual = scheduler.reset(start, count).run()
                        self.assertEqual(actual["checked"], count)
                        self.assertEqual(actual["cpu_checked"], count)
                        self.assertEqual(actual["gpu_checked"], 0)
                        self.assertEqual(actual["found"], 0)
                        self.assertEqual(actual["status"], 0)
                        self.assert_coverage(scheduler, start, count)

    def test_real_proof_and_full_work_use_independent_hashes(self):
        message = "a\x00한글🙂\n"
        for search in (False, True):
            with NativeScheduler(message, 1, workers=2, cpu_batch=37) as scheduler:
                result = scheduler.reset(0, 500, search=search).run()
                self.assertEqual(result["found"], 1)
                nonce = int(result["nonce"])
                expected = sha256(message.encode("utf-8") + str(nonce).encode("ascii")).hexdigest()
                self.assertEqual(result["hash"], expected)
                self.assertTrue(expected.startswith("0"))
                if not search:
                    self.assertEqual(result["checked"], 500)
                    self.assert_coverage(scheduler, 0, 500)

    def test_failed_gpu_retries_unconfirmed_range_without_gaps(self):
        calls = []

        def failing_gpu(start, count):
            calls.append((start, count))
            raise RuntimeError("intentional fake GPU failure")

        with NativeScheduler("failure vector", 64, workers=1, cpu_batch=8,
                             gpu_batch=100_000, trace_capacity=50_000) as scheduler:
            result = scheduler.reset(1_000_000_000, 200_000).run_with_callback(failing_gpu)
            self.assertTrue(calls)
            self.assertEqual(result["gpu_failed"], 1)
            self.assertEqual(result["gpu_checked"], 0)
            self.assertEqual(result["cpu_checked"], 200_000)
            self.assertGreater(result["retries"], 0)
            self.assertTrue(result["callback_errors"])
            self.assert_coverage(scheduler, 1_000_000_000, 200_000)

    def test_proof_survives_later_gpu_failure(self):
        message, calls = "preserved native proof", []

        def gpu(start, count):
            calls.append(start)
            if len(calls) > 1:
                raise RuntimeError("failure after a verified proof")
            return scan_batch(message, 1, start, count, search=False)

        with NativeScheduler(message, 1, workers=0, gpu_batch=1000) as scheduler:
            result = scheduler.reset(1000, 3000).run_with_callback(gpu)
            self.assertEqual(len(calls), 2)
            self.assertEqual(result["found"], 1)
            self.assertEqual(result["gpu_checked"], 1000)
            self.assertTrue(result["cleanup_warning"])
            self.assertEqual(result["hash"], sha256(message.encode() + result["nonce"].encode("ascii")).hexdigest())

    def test_gpu_failure_without_workers_is_a_fatal_scan_error(self):
        def gpu(_start, _count):
            raise RuntimeError("no CPU fallback")

        with NativeScheduler("fatal vector", 64, workers=0) as scheduler:
            with self.assertRaises(NativeRunError) as failure:
                scheduler.reset(0, 100).run_with_callback(gpu)
            self.assertEqual(failure.exception.stats["checked"], 0)
            self.assertEqual(failure.exception.stats["found"], 0)

    def test_cancel_join_then_restart_without_stale_results(self):
        with NativeScheduler("cancel vector", 64, workers=2, cpu_batch=1000) as scheduler:
            values, failures = [], []

            def run():
                try:
                    values.append(scheduler.run())
                except BaseException as error:
                    failures.append(error)

            scheduler.reset(0, 1 << 40)
            thread = threading.Thread(target=run, daemon=True)
            thread.start()
            deadline = time.monotonic() + 5
            while not scheduler.stats()["running"] and time.monotonic() < deadline:
                time.sleep(.001)
            scheduler.cancel()
            thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertFalse(failures, failures)
            self.assertEqual(len(values), 1)
            self.assertEqual(values[0]["cancelled"], 1)
            self.assertLess(values[0]["checked"], 1 << 40)
            result = scheduler.reset(MAX_NONCE - 10, 11).run()
            self.assertEqual(result["checked"], 11)
            self.assertEqual(result["cancelled"], 0)
            self.assert_coverage(scheduler, MAX_NONCE - 10, 11)

    def test_truncated_trace_is_explicit_and_does_not_change_counts(self):
        with NativeScheduler("trace vector", 64, workers=2, cpu_batch=1, trace_capacity=2) as scheduler:
            result = scheduler.reset(0, 19).run()
            self.assertEqual(result["checked"], 19)
            self.assertEqual(result["trace_count"], 2)
            self.assertEqual(result["trace_dropped"], 17)
            self.assertEqual(len(scheduler.traces()), 2)

    def test_invalid_inputs_rejected_before_unsigned_coercion(self):
        for arguments in ((b"x", 1), ("\ud800", 1), ("x", 0), ("x", True)):
            with self.subTest(arguments=ascii(arguments)), self.assertRaises(ValueError):
                NativeScheduler(*arguments)
        for options in ({"workers": True}, {"workers": 65}, {"cpu_batch": 0},
                        {"gpu_batch": 1 << 32}, {"trace_capacity": -1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                NativeScheduler("x", 1, **options)
        with NativeScheduler("x", 1) as scheduler:
            for start, count in ((-1, 1), (True, 1), (0, 0), (0, True), (MAX_NONCE, 2)):
                with self.subTest(start=start, count=count), self.assertRaises(ValueError):
                    scheduler.reset(start, count)
            with self.assertRaises(ValueError):
                scheduler.reset(0, 1, search=1)


@unittest.skipUnless(selected_dll().is_file() and os.environ.get("HASHCASH_TEST_NATIVE_CUDA") == "1",
                     "Actual CUDA test is opt-in and must run under the single GPU operator")
class NativeCudaSchedulerTests(unittest.TestCase):
    def test_native_driver_decimal_boundaries_and_uint64_digest_transport(self):
        from hashcash_lab.gpu import CudaBackend
        message = "한글🙂\n"
        backend = CudaBackend(message, 1)
        with NativeScheduler(message, 1, workers=0, gpu_batch=127) as scheduler:
            scheduler.attach_gpu(backend)
            for start, count in ((0, 37), (98, 207), (10 ** 19 - 3, 517), (MAX_NONCE - 512, 513)):
                with self.subTest(start=start):
                    stats = scheduler.reset(start, count, search=False).run()
                    self.assertEqual(stats["checked"], count)
                    self.assertEqual(stats["gpu_checked"], count)
                    self.assertEqual(stats["gpu_failed"], 0)
                    NativeSchedulerTests.assert_coverage(self, scheduler, start, count)
                    for trace in scheduler.traces():
                        first = int(trace["start"])
                        self.assertEqual(len(str(first)), len(str(first + trace["count"] - 1)))
                        expected = sha256(message.encode() + str(first + trace["checked"] - 1).encode("ascii")).hexdigest()
                        self.assertEqual(trace["last_hash"], expected)
                    if count >= 500:
                        self.assertTrue(stats["found"])
                        self.assertEqual(sha256(message.encode() + stats["nonce"].encode("ascii")).hexdigest(), stats["hash"])

    def test_real_cuda_driver_and_cpu_exact_coverage_and_proof(self):
        from hashcash_lab.gpu import CudaBackend
        message = "한글🙂\n"
        backend = CudaBackend(message, 1)
        with NativeScheduler(message, 1, workers=1, cpu_batch=100,
                             gpu_batch=16384) as scheduler:
            scheduler.attach_gpu(backend)
            result = scheduler.reset(1_000_000_000, 1 << 20).run()
            self.assertEqual(result["checked"], 1 << 20)
            self.assertGreater(result["gpu_checked"], 0)
            self.assertGreater(result["cpu_checked"], 0)
            self.assertGreater(result["gpu_kernel_seconds"], 0)
            self.assertEqual(result["found"], 1)
            self.assertEqual(result["gpu_failed"], 0)
            NativeSchedulerTests.assert_coverage(self, scheduler, 1_000_000_000, 1 << 20)


if __name__ == "__main__":
    unittest.main()
