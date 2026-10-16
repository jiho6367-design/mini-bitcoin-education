"""Coordinator correctness without importing CuPy or running GPU workloads."""
import tempfile
import threading
import time
import unittest
from hashlib import sha256
from multiprocessing.pool import ThreadPool
from unittest.mock import patch

from hashcash_lab import hybrid
from hashcash_lab.cpu import MAX_NONCE, scan_batch
from hashcash_lab.history import HistoryStore
from hashcash_lab.server import SearchConfig, SearchJob


class ReferenceGPU:
    name = "Reference test GPU"

    def __init__(self, message="message", difficulty=64):
        self.message, self.difficulty = message, difficulty
        self.calls = []
        self.closed = False

    def scan_batch(self, start, count):
        self.calls.append((start, count))
        return scan_batch(self.message, self.difficulty, start, count, search=False)

    def close(self):
        self.closed = True


def thread_pool(size):
    return ThreadPool(size)


class CoordinatorTests(unittest.TestCase):
    def coordinator(self, message="message", difficulty=64, workers=2, **kwargs):
        return hybrid.HybridCoordinator(message, difficulty, workers,
                                        pool_factory=thread_pool, **kwargs)

    def assert_exact_coverage(self, result, start, count):
        visited = []
        for timing in result.timings:
            self.assertEqual(timing.checked, timing.count)
            visited.extend(range(timing.start, timing.start + timing.count))
        self.assertEqual(sorted(visited), list(range(start, start + count)))
        self.assertEqual(len(visited), len(set(visited)))
        self.assertEqual(result.checked, count)
        self.assertEqual(result.checked, result.cpu_checked + result.gpu_checked)

    def test_fixed_work_is_same_unique_set_and_does_not_stop_on_a_proof(self):
        gpu = ReferenceGPU(difficulty=1)
        result = self.coordinator(difficulty=1, cpu_batch=13, gpu_batch=23).run(
            gpu, 98, 1003, search=False)
        self.assert_exact_coverage(result, 98, 1003)
        self.assertFalse(result.found)
        self.assertGreater(result.cpu_checked, 0)
        self.assertGreater(result.gpu_checked, 0)

    def test_gpu_reservations_clamp_before_decimal_and_uint64_boundaries(self):
        for start, count in ((98, 107), (10 ** 19 - 3, 17), (MAX_NONCE - 16, 17)):
            with self.subTest(start=start):
                gpu = ReferenceGPU()
                result = self.coordinator(cpu_batch=3, gpu_batch=7).run(gpu, start, count, search=False)
                self.assert_exact_coverage(result, start, count)
                for first, size in gpu.calls:
                    self.assertEqual(len(str(first)), len(str(first + size - 1)))
                    self.assertLessEqual(first + size - 1, MAX_NONCE)

    def test_real_spawn_workers_cover_reserved_ranges_and_report_cpu_times(self):
        runner = hybrid.HybridCoordinator("message", 64, 2, cpu_batch=300, gpu_batch=700)
        result = runner.run(ReferenceGPU(), 10000, 10000, search=False)
        self.assert_exact_coverage(result, 10000, 10000)
        self.assertGreater(result.cpu_checked, 0)
        self.assertGreater(result.gpu_checked, 0)
        for item in result.timings:
            self.assertGreaterEqual(item.finished, item.started)

    def test_gpu_failure_retries_only_failed_range_while_cpu_tasks_finish(self):
        class FailingGPU(ReferenceGPU):
            def scan_batch(self, start, count):
                batch = super().scan_batch(start, count)
                if len(self.calls) == 2:
                    raise RuntimeError("deliberate launch failure")
                return batch

        gpu = FailingGPU()
        failures = []
        result = self.coordinator(cpu_batch=3, gpu_batch=7).run(
            gpu, 96, 103, search=False, on_gpu_failure=failures.append)
        self.assert_exact_coverage(result, 96, 103)
        self.assertEqual(len(gpu.calls), 2)
        failed_first, failed_count = gpu.calls[-1]
        cpu_visits = {nonce for item in result.timings if item.device == "cpu"
                      for nonce in range(item.start, item.start + item.checked)}
        self.assertTrue(set(range(failed_first, failed_first + failed_count)) <= cpu_visits)
        self.assertEqual(result.gpu_checked, gpu.calls[0][1])
        self.assertIn("deliberate launch failure", result.gpu_error)
        self.assertEqual(failures, [result.gpu_error])

    def test_malformed_gpu_result_is_not_counted_and_range_retried(self):
        mutations = (
            lambda batch: {**batch, "checked": 0},
            lambda batch: {**batch, "checked": True},
            lambda batch: {**batch, "checked": batch["checked"] - 1},
            lambda batch: {**batch, "last_nonce": batch["last_nonce"] + 1},
            lambda batch: {**batch, "last_hash": "0" * 64},
            lambda batch: {**batch, "nonce": 0, "hash": "0" * 64},
            lambda batch: {**batch, "nonce": True, "hash": "0" * 64},
            lambda batch: {**batch, "hash": "0" * 64},
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                class IncorrectGPU(ReferenceGPU):
                    def scan_batch(self, start, count):
                        return mutate(super().scan_batch(start, count))

                result = self.coordinator(cpu_batch=5, gpu_batch=7).run(
                    IncorrectGPU(), 100, 91, search=False)
                self.assert_exact_coverage(result, 100, 91)
                self.assertEqual(result.gpu_checked, 0)
                self.assertIsNotNone(result.gpu_error)

    def test_worker_timestamps_prove_overlap_and_external_pool_is_reusable(self):
        cpu_running = threading.Event()
        gpu_running = threading.Event()
        original = hybrid.timed_cpu_scan

        def controlled_cpu(*args):
            began = time.perf_counter()
            cpu_running.set()
            self.assertTrue(gpu_running.wait(timeout=3))
            batch, _, _ = original(*args)
            time.sleep(0.005)
            return batch, began, time.perf_counter()

        class CoordinatedGPU(ReferenceGPU):
            def scan_batch(self, start, count):
                self.assert_cpu_started = cpu_running.wait(timeout=3)
                gpu_running.set()
                time.sleep(0.005)
                return super().scan_batch(start, count)

        gpu = CoordinatedGPU()
        pool = ThreadPool(1)
        try:
            with patch.object(hybrid, "timed_cpu_scan", side_effect=controlled_cpu):
                result = self.coordinator(workers=1, cpu_batch=10, gpu_batch=10).run(
                    gpu, 100, 100, search=False, pool=pool)
            self.assertTrue(gpu.assert_cpu_started)
            self.assertGreater(result.overlap, 0)
            self.assertEqual(pool.apply(sum, ([1, 2, 3],)), 6)
            self.assert_exact_coverage(result, 100, 100)
        finally:
            pool.terminate()
            pool.join()

    def test_cancelled_batch_reports_completed_work_and_joins_owned_pool(self):
        cancel = threading.Event()
        pools = []

        def factory(size):
            pool = ThreadPool(size)
            pools.append(pool)
            return pool

        class CancellingGPU(ReferenceGPU):
            def scan_batch(self, start, count):
                batch = super().scan_batch(start, count)
                cancel.set()
                return batch

        runner = hybrid.HybridCoordinator("message", 64, 1, cpu_batch=5,
                                          gpu_batch=7, pool_factory=factory)
        result = runner.run(CancellingGPU(), 100, 1000, search=False, cancel=cancel)
        self.assertTrue(result.stopped)
        self.assertEqual(result.gpu_checked, 7)
        self.assertLess(result.checked, 1000)
        self.assertTrue(all(not worker.is_alive() for worker in pools[0]._pool))

    def test_cancel_before_start_does_not_create_processes(self):
        cancel = threading.Event()
        cancel.set()
        runner = hybrid.HybridCoordinator("message", 64, 1,
                                          pool_factory=lambda _: self.fail("created a pool"))
        result = runner.run(ReferenceGPU(), 0, 100, cancel=cancel)
        self.assertTrue(result.stopped)
        self.assertEqual(result.checked, 0)

    def test_finite_gpu_exhaustion_yields_until_result_delivery_and_allows_cancel(self):
        # Deliberately hold a completed worker result until the coordinator
        # yields. This models a Pool result-handler waiting for the GIL.
        class DelayedResult:
            ready_checks = 0

            def ready(self):
                self.ready_checks += 1
                if self.ready_checks > 10:
                    raise AssertionError("busy-spinning prevented result delivery")
                return released.is_set()

            def get(self):
                return hybrid.timed_cpu_scan("message", 64, 107, 3, False)

        class Pool:
            def apply_async(self, _worker, _args):
                return DelayedResult()

        for stop in (False, True):
            with self.subTest(cancel=stop):
                released = threading.Event()
                cancel = threading.Event()

                def yield_control(_timeout):
                    released.set()
                    if stop:
                        cancel.set()

                with patch.object(cancel, "wait", side_effect=yield_control) as wait:
                    result = self.coordinator(workers=1, cpu_batch=3, gpu_batch=7).run(
                        ReferenceGPU(), 100, 10, search=False, cancel=cancel, pool=Pool())
                self.assertTrue(wait.called)
                self.assertEqual(result.stopped, stop)
                self.assert_exact_coverage(result, 100, 10)

    def test_cpu_worker_failure_is_not_hidden_as_gpu_fallback(self):
        def broken_worker(*_):
            raise RuntimeError("CPU worker failed")

        with patch.object(hybrid, "timed_cpu_scan", side_effect=broken_worker):
            with self.assertRaisesRegex(RuntimeError, "CPU worker failed"):
                self.coordinator(cpu_batch=3, gpu_batch=7).run(
                    ReferenceGPU(), 100, 70, search=False)

    def test_application_saves_one_verified_proof_after_cleanup(self):
        class Context:
            Pool = staticmethod(thread_pool)

        gpu = ReferenceGPU(difficulty=1)
        with tempfile.TemporaryDirectory() as directory:
            history = HistoryStore(directory)
            job = SearchJob(SearchConfig("message", 1, 0, 1, "hybrid", "동시 실험"),
                            lambda *_: gpu, history)
            with patch.object(hybrid.mp, "get_context", return_value=Context()), \
                    patch("hashcash_lab.server.GPU_BATCH", 10), \
                    patch("hashcash_lab.server.CPU_BATCH", 8):
                job.run()
            state = job.snapshot()
            self.assertEqual(state["status"], "found", state)
            self.assertEqual(state["mode"], "hybrid")
            nonce = state["result"]["nonce"]
            expected = sha256(b"message" + nonce.encode("ascii")).hexdigest()
            self.assertEqual(state["result"]["hash"], expected)
            self.assertTrue(expected.startswith("0"))
            self.assertEqual(int(state["checked"]), int(state["cpuChecked"]) + int(state["gpuChecked"]))
            self.assertEqual(history.query()["total"], 1)
            self.assertEqual(history.query()["records"][0]["backend"], state["backend"])
            self.assertTrue(gpu.closed)
            job.save_result()
            self.assertEqual(history.query()["total"], 1)

    def test_application_gpu_launch_failure_uses_cpu_without_counting_failed_gpu(self):
        class Context:
            Pool = staticmethod(thread_pool)

        class FailingGPU(ReferenceGPU):
            def scan_batch(self, *_):
                raise RuntimeError("failed first GPU range")

        job = SearchJob(SearchConfig("message", 1, 0, 1, "hybrid"), lambda *_: FailingGPU())
        with patch.object(hybrid.mp, "get_context", return_value=Context()), \
                patch("hashcash_lab.server.GPU_BATCH", 10), \
                patch("hashcash_lab.server.CPU_BATCH", 8):
            job.run()
        state = job.snapshot()
        self.assertEqual(state["status"], "found", state)
        self.assertEqual(state["gpuChecked"], "0")
        self.assertEqual(state["cpuChecked"], state["checked"])
        self.assertIn("미완료 구간 CPU 재시도", state["reason"])
        self.assertIn("CPU", state["backend"])
        self.assertNotIn("CPU + GPU", state["backend"])

    def test_application_cpu_error_stops_without_restarting_already_counted_work(self):
        class Context:
            Pool = staticmethod(thread_pool)

        def broken_worker(*_):
            raise RuntimeError("CPU worker died")

        job = SearchJob(SearchConfig("message", 64, 100, 1, "hybrid"), lambda *_: ReferenceGPU())
        with patch.object(hybrid.mp, "get_context", return_value=Context()), \
                patch.object(hybrid, "timed_cpu_scan", side_effect=broken_worker), \
                patch("hashcash_lab.server.GPU_BATCH", 10), \
                patch("hashcash_lab.server.CPU_BATCH", 8), \
                patch.object(job, "run_cpu", side_effect=AssertionError("restarted counted work")):
            job.run()
        state = job.snapshot()
        self.assertEqual(state["status"], "error")
        self.assertIn("CPU worker died", state["error"])
        self.assertGreater(int(state["gpuChecked"]), 0)
        self.assertEqual(state["cpuChecked"], "0")

    def test_verified_gpu_proof_survives_late_cpu_failure_and_is_saved_once(self):
        gpu_computed = threading.Event()

        class Context:
            Pool = staticmethod(thread_pool)

        class ProofGPU(ReferenceGPU):
            def scan_batch(self, start, count):
                batch = super().scan_batch(start, count)
                gpu_computed.set()
                time.sleep(0.01)
                return batch

        def late_failure(*_):
            self.assertTrue(gpu_computed.wait(timeout=3))
            raise RuntimeError("late CPU failure")

        with tempfile.TemporaryDirectory() as directory:
            history = HistoryStore(directory)
            job = SearchJob(SearchConfig("message", 1, 100, 1, "hybrid", "review"),
                            lambda *_: ProofGPU(difficulty=1), history)
            with patch.object(hybrid.mp, "get_context", return_value=Context()), \
                    patch.object(hybrid, "timed_cpu_scan", side_effect=late_failure), \
                    patch("hashcash_lab.server.GPU_BATCH", 100), \
                    patch("hashcash_lab.server.CPU_BATCH", 8):
                job.run()
            state = job.snapshot()
            self.assertEqual(state["status"], "found", state)
            self.assertEqual(state["result"]["nonce"], "116")
            self.assertTrue(state["result"]["verified"])
            self.assertIsNone(state["error"])
            self.assertIn("late CPU failure", state["cleanupWarning"])
            self.assertEqual(state["gpuChecked"], "100")
            self.assertEqual(state["cpuChecked"], "0")
            self.assertEqual(history.query()["total"], 1)
            self.assertEqual(history.query()["records"][0]["nonce"], "116")
            job.save_result()
            self.assertEqual(history.query()["total"], 1)

    def test_bounded_trace_does_not_change_exact_counts_and_full_trace_is_opt_in(self):
        spans = []
        runner = self.coordinator(cpu_batch=3, gpu_batch=7)
        result = runner.run(ReferenceGPU(), 1000, 1003, search=False, trace_limit=5,
                            on_batch=lambda _device, _batch, timing: spans.append(timing))
        self.assertTrue(result.timings_truncated)
        self.assertEqual(len(result.timings), 5)
        self.assertEqual(list(result.timings), spans[-5:])
        self.assertEqual(result.checked, 1003)
        visited = [nonce for item in spans for nonce in range(item.start, item.start + item.checked)]
        self.assertEqual(sorted(visited), list(range(1000, 2003)))
        self.assertLessEqual(result.overlap, hybrid.overlap_seconds(spans))
        all_traces = runner.run(ReferenceGPU(), 1000, 1003, search=False, trace_limit=None)
        self.assertFalse(all_traces.timings_truncated)
        self.assertGreater(len(all_traces.timings), 5)
        self.assert_exact_coverage(all_traces, 1000, 1003)

    def test_overlap_unions_cpu_intervals_instead_of_double_counting_cores(self):
        traces = [hybrid.BatchTiming("cpu", 0, 1, 1, 1.0, 5.0),
                  hybrid.BatchTiming("cpu", 1, 1, 1, 2.0, 4.0),
                  hybrid.BatchTiming("gpu", 2, 1, 1, 3.0, 6.0)]
        self.assertEqual(hybrid.overlap_seconds(traces), 2.0)
        self.assertEqual(traces[0].json()["start"], "0")

    def test_kernel_overlap_bound_subtracts_all_possible_non_kernel_time(self):
        traces = [hybrid.BatchTiming("cpu", 0, 1, 1, 1.0, 5.0),
                  hybrid.BatchTiming("cpu", 1, 1, 1, 2.0, 4.0),
                  hybrid.BatchTiming("gpu", 2, 1, 1, 3.0, 6.0, 2.5)]
        self.assertEqual(hybrid.overlap_seconds(traces), 2.0)
        self.assertEqual(hybrid.kernel_overlap_lower_bound(traces), 1.5)
        result = hybrid.HybridResult(timings=traces)
        self.assertEqual(result.kernel_overlap_lower_bound, 1.5)
        uncertain = [hybrid.BatchTiming("cpu", 0, 1, 1, 1.0, 2.0),
                     hybrid.BatchTiming("gpu", 1, 1, 1, 0.0, 10.0, 2.0)]
        self.assertEqual(hybrid.kernel_overlap_lower_bound(uncertain), 0.0)

    def test_absent_invalid_and_oversized_kernel_metadata_cannot_prove_overlap(self):
        cpu = hybrid.BatchTiming("cpu", 0, 1, 1, 0.0, 10.0)
        for kernel in (0.0, None, True, "1", -1.0, float("nan"), float("inf"), 11.0):
            with self.subTest(kernel=kernel):
                gpu = hybrid.BatchTiming("gpu", 1, 1, 1, 0.0, 10.0, kernel)
                self.assertEqual(hybrid.kernel_overlap_lower_bound([cpu, gpu]), 0.0)
        fake = self.coordinator(cpu_batch=3, gpu_batch=7).run(
            ReferenceGPU(), 1000, 100, search=False)
        self.assertEqual(fake.kernel_overlap_lower_bound, 0.0)

    def test_invalid_finite_work_and_constructor_parameters_fail_early(self):
        runner = self.coordinator()
        for first, count in ((-1, 1), (MAX_NONCE, 2), (0, 0), (0, True)):
            with self.subTest(first=first, count=count), self.assertRaises(ValueError):
                runner.run(ReferenceGPU(), first, count, search=False)
        for option in ({"workers": 0}, {"cpu_batch": 0}, {"gpu_batch": 1 << 32}):
            with self.subTest(option=option), self.assertRaises(ValueError):
                self.coordinator(**option)
        for limit in (0, -1, True, 1.5):
            with self.subTest(trace_limit=limit), self.assertRaises(ValueError):
                runner.run(ReferenceGPU(), 0, 10, search=False, trace_limit=limit)


if __name__ == "__main__":
    unittest.main()
